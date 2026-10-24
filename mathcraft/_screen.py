"""Small VT screen model for the terminal widget; no third-party imports."""
from collections import deque


KEYS = {'Return':'\r', 'KP_Enter':'\r', 'BackSpace':'\x7f', 'Tab':'\t',
        'Escape':'\x1b', 'Up':'\x1b[A', 'Down':'\x1b[B', 'Right':'\x1b[C',
        'Left':'\x1b[D', 'Home':'\x1b[H', 'End':'\x1b[F', 'Insert':'\x1b[2~',
        'Delete':'\x1b[3~', 'Prior':'\x1b[5~', 'Next':'\x1b[6~',
        'F1':'\x1bOP','F2':'\x1bOQ','F3':'\x1bOR','F4':'\x1bOS',
        'F5':'\x1b[15~','F6':'\x1b[17~','F7':'\x1b[18~','F8':'\x1b[19~',
        'F9':'\x1b[20~','F10':'\x1b[21~','F11':'\x1b[23~','F12':'\x1b[24~'}


def encode_key(key, text='', shift=False, control=False, alt=False):
    if key in ('Shift_L','Shift_R','Control_L','Control_R','Alt_L','Alt_R',
               'Meta_L','Meta_R','Super_L','Super_R','Caps_Lock','Num_Lock'):
        return ''
    if key=='Tab' and shift:
        return '\x1b[Z'
    if control and len(key)==1 and '@'<=key.upper()<='_':
        return chr(ord(key.upper()) & 31)
    if control and key in ('Left','Right','Up','Down'):
        return '\x1b[1;5'+{'Left':'D','Right':'C','Up':'A','Down':'B'}[key]
    value=KEYS.get(key,text)
    return ('\x1b'+value) if alt and value else value


class Cell:
    __slots__=('data','fg','bg','bold','underscore','reverse')

    def __init__(self, data=' ', fg='default', bg='default', bold=False,
                 underscore=False, reverse=False):
        self.data=data
        self.fg=fg
        self.bg=bg
        self.bold=bold
        self.underscore=underscore
        self.reverse=reverse

    def copy(self, data=None):
        return Cell(self.data if data is None else data, self.fg, self.bg,
                    self.bold, self.underscore, self.reverse)


class Cursor:
    __slots__=('x','y','hidden')

    def __init__(self):
        self.x=0
        self.y=0
        self.hidden=False


class TerminalScreen:
    """VT subset used by the PowerShell UI, with bounded scrollback."""
    def __init__(self, columns, lines, send):
        self.columns=max(2,int(columns))
        self.lines=max(2,int(lines))
        self.send=send
        self.cursor=Cursor()
        self._attrs=Cell()
        self.buffer=[self._blank_line() for _ in range(self.lines)]
        self.dirty=set(range(self.lines))
        self.history=deque(maxlen=1000)
        self._view_offset=0
        self._state='ground'
        self._sequence=''
        self._saved=(0,0)
        self._scroll_top=0
        self._scroll_bottom=self.lines-1

    def _blank_cell(self):
        return self._attrs.copy()

    def _blank_line(self):
        return [self._blank_cell() for _ in range(self.columns)]

    @property
    def display(self):
        rows=self.buffer
        if self._view_offset:
            combined=list(self.history)+rows
            start=max(0,len(combined)-self.lines-self._view_offset)
            rows=combined[start:start+self.lines]
            rows=rows+[self._blank_line() for _ in range(self.lines-len(rows))]
        return [''.join(cell.data for cell in row) for row in rows]

    def resize(self, lines, columns):
        lines=max(2,min(256,int(lines)))
        columns=max(2,min(512,int(columns)))
        if columns!=self.columns:
            for row in self.buffer:
                row[:]=row[:columns]+[self._blank_cell() for _ in range(max(0,columns-len(row)))]
            self.columns=columns
            self.cursor.x=min(self.cursor.x,columns-1)
        if lines<self.lines:
            removed=self.buffer[:self.lines-lines]
            self.history.extend(removed)
            self.buffer=self.buffer[self.lines-lines:]
        elif lines>self.lines:
            self.buffer=[self._blank_line() for _ in range(lines-self.lines)]+self.buffer
            self.cursor.y+=lines-self.lines
        self.lines=lines
        self.cursor.y=min(self.cursor.y,lines-1)
        self._scroll_top=0
        self._scroll_bottom=lines-1
        self.dirty.update(range(lines))

    def prev_page(self):
        self._view_offset=min(len(self.history),self._view_offset+max(1,self.lines-1))
        self.dirty.update(range(self.lines))

    def next_page(self):
        self._view_offset=max(0,self._view_offset-max(1,self.lines-1))
        self.dirty.update(range(self.lines))

    def feed(self, data):
        if not isinstance(data,str):
            raise TypeError('terminal data must be str')
        for char in data:
            self._consume(char)

    def write_process_input(self, data):
        self.send(data)

    def _consume(self, char):
        state=self._state
        if state=='esc':
            self._state='ground'
            if char=='[':
                self._state='csi';self._sequence=''
            elif char==']':
                self._state='osc';self._sequence=''
            elif char=='7':self._saved=(self.cursor.x,self.cursor.y)
            elif char=='8':self._set_cursor(*self._saved)
            elif char=='D':self._linefeed()
            elif char=='E':self.cursor.x=0;self._linefeed()
            elif char=='M':self._reverse_index()
            elif char=='c':self._reset()
            return
        if state=='csi':
            if '\x40'<=char<='\x7e':
                self._csi(self._sequence,char);self._state='ground';self._sequence=''
            elif len(self._sequence)<128:
                self._sequence+=char
            else:
                self._state='ground';self._sequence=''
            return
        if state=='osc':
            if char=='\x07':self._state='ground'
            elif char=='\x1b':self._state='osc_esc'
            return
        if state=='osc_esc':
            self._state='ground' if char=='\\' else 'osc'
            return
        code=ord(char)
        if char=='\x1b':self._state='esc'
        elif char=='\r':self.cursor.x=0
        elif char in ('\n','\v','\f'):self._linefeed()
        elif char=='\b':self.cursor.x=max(0,self.cursor.x-1)
        elif char=='\t':self.cursor.x=min(self.columns-1,(self.cursor.x//8+1)*8)
        elif code>=32 and code!=127:self._put(char)

    def _put(self,char):
        if self.cursor.x>=self.columns:
            self.cursor.x=0;self._linefeed()
        self.buffer[self.cursor.y][self.cursor.x]=self._attrs.copy(char)
        self.dirty.add(self.cursor.y)
        self.cursor.x+=1

    def _set_cursor(self,x,y):
        self.cursor.x=max(0,min(self.columns-1,int(x)))
        self.cursor.y=max(0,min(self.lines-1,int(y)))

    def _linefeed(self):
        self._view_offset=0
        if self.cursor.y==self._scroll_bottom:
            self._scroll_up(1)
        else:
            self.cursor.y=min(self.lines-1,self.cursor.y+1)

    def _reverse_index(self):
        if self.cursor.y==self._scroll_top:self._scroll_down(1)
        else:self.cursor.y=max(0,self.cursor.y-1)

    def _scroll_up(self,count):
        for _ in range(min(count,self.lines)):
            row=self.buffer.pop(self._scroll_top)
            if self._scroll_top==0 and self._scroll_bottom==self.lines-1:
                self.history.append(row)
            self.buffer.insert(self._scroll_bottom,self._blank_line())
        self.dirty.update(range(self._scroll_top,self._scroll_bottom+1))

    def _scroll_down(self,count):
        for _ in range(min(count,self.lines)):
            self.buffer.pop(self._scroll_bottom)
            self.buffer.insert(self._scroll_top,self._blank_line())
        self.dirty.update(range(self._scroll_top,self._scroll_bottom+1))

    def _params(self,sequence):
        private=sequence.startswith('?')
        if private:sequence=sequence[1:]
        values=[]
        for item in sequence.split(';') if sequence else []:
            try:values.append(int(item or '0'))
            except ValueError:values.append(0)
        return private,values

    def _csi(self,sequence,final):
        private,p=self._params(sequence)
        n=lambda index,default=1:(p[index] if index<len(p) and p[index] else default)
        x,y=self.cursor.x,self.cursor.y
        if final=='A':self._set_cursor(x,y-n(0))
        elif final=='B':self._set_cursor(x,y+n(0))
        elif final=='C':self._set_cursor(x+n(0),y)
        elif final=='D':self._set_cursor(x-n(0),y)
        elif final=='E':self._set_cursor(0,y+n(0))
        elif final=='F':self._set_cursor(0,y-n(0))
        elif final=='G':self._set_cursor(n(0)-1,y)
        elif final=='d':self._set_cursor(x,n(0)-1)
        elif final in ('H','f'):self._set_cursor(n(1)-1,n(0)-1)
        elif final=='J':self._erase_display(p[0] if p else 0)
        elif final=='K':self._erase_line(p[0] if p else 0)
        elif final=='m':self._sgr(p)
        elif final=='s':self._saved=(x,y)
        elif final=='u':self._set_cursor(*self._saved)
        elif final=='r':
            self._scroll_top=max(0,min(self.lines-1,n(0)-1))
            self._scroll_bottom=max(self._scroll_top,min(self.lines-1,n(1,self.lines)-1))
            self._set_cursor(0,0)
        elif final=='S':self._scroll_up(n(0))
        elif final=='T':self._scroll_down(n(0))
        elif final=='@':self._insert_chars(n(0))
        elif final=='P':self._delete_chars(n(0))
        elif final=='X':self._erase_chars(n(0))
        elif final=='L':self._insert_lines(n(0))
        elif final=='M':self._delete_lines(n(0))
        elif final in ('h','l') and private and 25 in p:self.cursor.hidden=final=='l'
        elif final=='n' and p and p[0]==5:self.write_process_input('\x1b[0n')
        elif final=='n' and p and p[0]==6:
            self.write_process_input('\x1b[{};{}R'.format(self.cursor.y+1,self.cursor.x+1))
        elif final=='c':self.write_process_input('\x1b[?1;2c')

    def _erase_line(self,mode):
        y,x=self.cursor.y,self.cursor.x
        start,end=(x,self.columns) if mode==0 else ((0,x+1) if mode==1 else (0,self.columns))
        self.buffer[y][start:end]=[self._blank_cell() for _ in range(end-start)]
        self.dirty.add(y)

    def _erase_display(self,mode):
        y=self.cursor.y
        if mode==2 or mode==3:
            self.buffer=[self._blank_line() for _ in range(self.lines)]
            if mode==3:self.history.clear()
            self.dirty.update(range(self.lines))
        elif mode==0:
            self._erase_line(0)
            for row in range(y+1,self.lines):self.buffer[row]=self._blank_line();self.dirty.add(row)
        elif mode==1:
            for row in range(y):self.buffer[row]=self._blank_line();self.dirty.add(row)
            self._erase_line(1)

    def _insert_chars(self,count):
        row=self.buffer[self.cursor.y];x=self.cursor.x
        row[x:x]=[self._blank_cell() for _ in range(min(count,self.columns))]
        del row[self.columns:]
        self.dirty.add(self.cursor.y)

    def _delete_chars(self,count):
        row=self.buffer[self.cursor.y];x=self.cursor.x
        del row[x:x+count]
        row.extend(self._blank_cell() for _ in range(self.columns-len(row)))
        self.dirty.add(self.cursor.y)

    def _erase_chars(self,count):
        row=self.buffer[self.cursor.y]
        for x in range(self.cursor.x,min(self.columns,self.cursor.x+count)):row[x]=self._blank_cell()
        self.dirty.add(self.cursor.y)

    def _insert_lines(self,count):
        for _ in range(min(count,self._scroll_bottom-self.cursor.y+1)):
            self.buffer.insert(self.cursor.y,self._blank_line());self.buffer.pop(self._scroll_bottom+1)
        self.dirty.update(range(self.cursor.y,self._scroll_bottom+1))

    def _delete_lines(self,count):
        for _ in range(min(count,self._scroll_bottom-self.cursor.y+1)):
            self.buffer.pop(self.cursor.y);self.buffer.insert(self._scroll_bottom,self._blank_line())
        self.dirty.update(range(self.cursor.y,self._scroll_bottom+1))

    def _sgr(self,values):
        values=values or [0]
        fg,bg,bold,underscore,reverse=(self._attrs.fg,self._attrs.bg,self._attrs.bold,
                                      self._attrs.underscore,self._attrs.reverse)
        palette=('black','red','green','brown','blue','magenta','cyan','white')
        i=0
        while i<len(values):
            value=values[i]
            if value==0:fg,bg,bold,underscore,reverse='default','default',False,False,False
            elif value==1:bold=True
            elif value==4:underscore=True
            elif value==7:reverse=True
            elif value==22:bold=False
            elif value==24:underscore=False
            elif value==27:reverse=False
            elif 30<=value<=37:fg=palette[value-30]
            elif 40<=value<=47:bg=palette[value-40]
            elif 90<=value<=97:fg='bright'+palette[value-90]
            elif 100<=value<=107:bg='bright'+palette[value-100]
            elif value==39:fg='default'
            elif value==49:bg='default'
            elif value in (38,48) and i+2<len(values) and values[i+1]==5:
                color=self._indexed_color(values[i+2]);
                if value==38:fg=color
                else:bg=color
                i+=2
            elif value in (38,48) and i+4<len(values) and values[i+1]==2:
                color='{:02x}{:02x}{:02x}'.format(*(max(0,min(255,v)) for v in values[i+2:i+5]))
                if value==38:fg=color
                else:bg=color
                i+=4
            i+=1
        self._attrs=Cell(fg=fg,bg=bg,bold=bold,underscore=underscore,reverse=reverse)

    @staticmethod
    def _indexed_color(index):
        if index<16:
            base=('black','red','green','brown','blue','magenta','cyan','white')
            return base[index%8] if index<8 else 'bright'+base[index%8]
        if index>=232:
            value=8+(index-232)*10
            return '{:02x}{:02x}{:02x}'.format(value,value,value)
        index-=16
        levels=(0,95,135,175,215,255)
        r,g,b=(levels[(index//36)%6],levels[(index//6)%6],levels[index%6])
        return '{:02x}{:02x}{:02x}'.format(r,g,b)

    def _reset(self):
        self.cursor=Cursor();self._attrs=Cell();self._view_offset=0
        self._scroll_top=0;self._scroll_bottom=self.lines-1
        self.buffer=[self._blank_line() for _ in range(self.lines)]
        self.dirty.update(range(self.lines))
