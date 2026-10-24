"""Windows ConPTY transport implemented with the Windows API."""
import codecs
import ctypes
from ctypes import wintypes as w
import queue
import shutil
import subprocess
import threading


class BasicLimits(ctypes.Structure):
    _fields_=[('process_time',ctypes.c_longlong),('job_time',ctypes.c_longlong),
              ('flags',w.DWORD),('min_ws',ctypes.c_size_t),('max_ws',ctypes.c_size_t),
              ('process_count',w.DWORD),('affinity',ctypes.c_size_t),
              ('priority',w.DWORD),('scheduling',w.DWORD)]


class Counters(ctypes.Structure):
    _fields_=[(name,ctypes.c_ulonglong) for name in
              ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]


class ExtendedLimits(ctypes.Structure):
    _fields_=[('basic',BasicLimits),('io',Counters),('process_memory',ctypes.c_size_t),
              ('job_memory',ctypes.c_size_t),('peak_process',ctypes.c_size_t),
              ('peak_job',ctypes.c_size_t)]


class Coord(ctypes.Structure):
    _fields_=[('x',w.SHORT),('y',w.SHORT)]


class StartupInfo(ctypes.Structure):
    _fields_=[('cb',w.DWORD),('reserved',w.LPWSTR),('desktop',w.LPWSTR),
              ('title',w.LPWSTR),('x',w.DWORD),('y',w.DWORD),
              ('x_size',w.DWORD),('y_size',w.DWORD),('x_count_chars',w.DWORD),
              ('y_count_chars',w.DWORD),('fill_attribute',w.DWORD),('flags',w.DWORD),
              ('show_window',w.WORD),('reserved_size',w.WORD),
              ('reserved_data',ctypes.POINTER(w.BYTE)),('stdin',w.HANDLE),
              ('stdout',w.HANDLE),('stderr',w.HANDLE)]


class StartupInfoEx(ctypes.Structure):
    _fields_=[('startup',StartupInfo),('attributes',ctypes.c_void_p)]


class ProcessInfo(ctypes.Structure):
    _fields_=[('process',w.HANDLE),('thread',w.HANDLE),('pid',w.DWORD),('tid',w.DWORD)]


class ProcessJob:
    def __init__(self):
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        specs={
            'CreateJobObjectW':([ctypes.c_void_p,w.LPCWSTR],w.HANDLE),
            'SetInformationJobObject':([w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD],w.BOOL),
            'AssignProcessToJobObject':([w.HANDLE,w.HANDLE],w.BOOL),
            'TerminateJobObject':([w.HANDLE,w.UINT],w.BOOL),
            'OpenProcess':([w.DWORD,w.BOOL,w.DWORD],w.HANDLE),
            'CloseHandle':([w.HANDLE],w.BOOL),
        }
        for name,(args,result) in specs.items():
            function=getattr(self.kernel,name)
            function.argtypes,function.restype=args,result
        self.handle=self.kernel.CreateJobObjectW(None,None)
        if not self.handle:raise ctypes.WinError(ctypes.get_last_error())
        limits=ExtendedLimits();limits.basic.flags=0x2000
        if not self.kernel.SetInformationJobObject(self.handle,9,ctypes.byref(limits),ctypes.sizeof(limits)):
            self.close();raise ctypes.WinError(ctypes.get_last_error())

    def attach(self,pid):
        process=self.kernel.OpenProcess(0x101,False,pid)
        if not process:raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self.kernel.AssignProcessToJobObject(self.handle,process):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:self.kernel.CloseHandle(process)

    def close(self):
        if self.handle:
            self.kernel.TerminateJobObject(self.handle,1)
            self.kernel.CloseHandle(self.handle)
            self.handle=None


class _ConPty:
    _EXTENDED_STARTUPINFO_PRESENT=0x00080000
    _PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE=0x00020016
    _HANDLE_FLAG_INHERIT=0x00000001
    _STILL_ACTIVE=259

    def __init__(self,argv,columns,rows):
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self._bind_api()
        self.hpc=ctypes.c_void_p()
        self.input_read=None
        self.input_write=None
        self.output_read=None
        self.output_write=None
        self.process=None
        self.thread=None
        self._decoder=codecs.getincrementaldecoder('utf-8')('replace')
        try:
            self._create_pipes()
            result=self.kernel.CreatePseudoConsole(Coord(columns,rows),self.input_read,
                                                   self.output_write,0,ctypes.byref(self.hpc))
            if result<0:
                raise OSError('CreatePseudoConsole failed (HRESULT 0x{:08X})'.format(result & 0xffffffff))
            executable=shutil.which(argv[0])
            if not executable:raise FileNotFoundError(argv[0]+' is not installed')
            command=subprocess.list2cmdline([executable]+list(argv[1:]))
            self._spawn(executable,command)
            self.pid=self._process_id
        except Exception:
            self.close(terminate=True)
            raise

    def _bind_api(self):
        k=self.kernel
        signatures={
            'CreatePipe':([ctypes.POINTER(w.HANDLE),ctypes.POINTER(w.HANDLE),ctypes.c_void_p,w.DWORD],w.BOOL),
            'SetHandleInformation':([w.HANDLE,w.DWORD,w.DWORD],w.BOOL),
            'CreatePseudoConsole':([Coord,w.HANDLE,w.HANDLE,w.DWORD,ctypes.POINTER(ctypes.c_void_p)],ctypes.c_long),
            'ResizePseudoConsole':([ctypes.c_void_p,Coord],ctypes.c_long),
            'ClosePseudoConsole':([ctypes.c_void_p],None),
            'InitializeProcThreadAttributeList':([ctypes.c_void_p,w.DWORD,w.DWORD,ctypes.POINTER(ctypes.c_size_t)],w.BOOL),
            'UpdateProcThreadAttribute':([ctypes.c_void_p,w.DWORD,ctypes.c_size_t,ctypes.c_void_p,ctypes.c_size_t,ctypes.c_void_p,ctypes.c_void_p],w.BOOL),
            'DeleteProcThreadAttributeList':([ctypes.c_void_p],None),
            'CreateProcessW':([w.LPCWSTR,w.LPWSTR,ctypes.c_void_p,ctypes.c_void_p,w.BOOL,w.DWORD,ctypes.c_void_p,w.LPCWSTR,ctypes.c_void_p,ctypes.POINTER(ProcessInfo)],w.BOOL),
            'CloseHandle':([w.HANDLE],w.BOOL),
            'PeekNamedPipe':([w.HANDLE,ctypes.c_void_p,w.DWORD,ctypes.c_void_p,ctypes.POINTER(w.DWORD),ctypes.c_void_p],w.BOOL),
            'ReadFile':([w.HANDLE,ctypes.c_void_p,w.DWORD,ctypes.POINTER(w.DWORD),ctypes.c_void_p],w.BOOL),
            'WriteFile':([w.HANDLE,ctypes.c_void_p,w.DWORD,ctypes.POINTER(w.DWORD),ctypes.c_void_p],w.BOOL),
            'CancelIoEx':([w.HANDLE,ctypes.c_void_p],w.BOOL),
            'WaitForSingleObject':([w.HANDLE,w.DWORD],w.DWORD),
            'GetExitCodeProcess':([w.HANDLE,ctypes.POINTER(w.DWORD)],w.BOOL),
            'TerminateProcess':([w.HANDLE,w.UINT],w.BOOL),
        }
        for name,(args,result) in signatures.items():
            fn=getattr(k,name);fn.argtypes=args;fn.restype=result

    def _create_pipes(self):
        self.input_read,self.input_write=w.HANDLE(),w.HANDLE()
        self.output_read,self.output_write=w.HANDLE(),w.HANDLE()
        if not self.kernel.CreatePipe(ctypes.byref(self.input_read),ctypes.byref(self.input_write),None,0):
            raise ctypes.WinError(ctypes.get_last_error())
        if not self.kernel.CreatePipe(ctypes.byref(self.output_read),ctypes.byref(self.output_write),None,0):
            raise ctypes.WinError(ctypes.get_last_error())
        for handle in (self.input_write,self.output_read):
            if not self.kernel.SetHandleInformation(handle,self._HANDLE_FLAG_INHERIT,0):
                raise ctypes.WinError(ctypes.get_last_error())

    def _spawn(self,executable,command):
        size=ctypes.c_size_t()
        self.kernel.InitializeProcThreadAttributeList(None,1,0,ctypes.byref(size))
        storage=ctypes.create_string_buffer(size.value)
        attributes=ctypes.cast(storage,ctypes.c_void_p)
        if not self.kernel.InitializeProcThreadAttributeList(attributes,1,0,ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            hpc=self.hpc
            if not self.kernel.UpdateProcThreadAttribute(attributes,0,
                    self._PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,hpc,ctypes.sizeof(hpc),None,None):
                raise ctypes.WinError(ctypes.get_last_error())
            startup=StartupInfoEx()
            startup.startup.cb=ctypes.sizeof(StartupInfoEx)
            startup.attributes=attributes
            info=ProcessInfo()
            command_line=ctypes.create_unicode_buffer(command)
            if not self.kernel.CreateProcessW(executable,command_line,None,None,True,
                    self._EXTENDED_STARTUPINFO_PRESENT,None,None,ctypes.byref(startup),ctypes.byref(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            self.process,self.thread=info.process,info.thread
            self._process_id=info.pid
        finally:self.kernel.DeleteProcThreadAttributeList(attributes)

    def write(self,text):
        data=text.encode('utf-8')
        offset=0
        while offset<len(data):
            written=w.DWORD()
            part=data[offset:offset+4096]
            if not self.kernel.WriteFile(self.input_write,part,len(part),ctypes.byref(written),None):
                raise ctypes.WinError(ctypes.get_last_error())
            if not written.value:raise OSError('ConPTY accepted zero input bytes')
            offset+=written.value

    def read(self):
        available=w.DWORD()
        if not self.kernel.PeekNamedPipe(self.output_read,None,0,None,ctypes.byref(available),None):
            error=ctypes.get_last_error()
            if error in (109,232):return ''
            raise ctypes.WinError(error)
        if not available.value:return ''
        buffer=ctypes.create_string_buffer(min(available.value,16384))
        read=w.DWORD()
        if not self.kernel.ReadFile(self.output_read,buffer,len(buffer),ctypes.byref(read),None):
            error=ctypes.get_last_error()
            if error in (109,232):return ''
            raise ctypes.WinError(error)
        return self._decoder.decode(buffer.raw[:read.value],final=False)

    def resize(self,columns,rows):
        result=self.kernel.ResizePseudoConsole(self.hpc,Coord(columns,rows))
        if result<0:raise OSError('ResizePseudoConsole failed (HRESULT 0x{:08X})'.format(result & 0xffffffff))

    def isalive(self):
        return bool(self.process and self.kernel.WaitForSingleObject(self.process,0)==0x102)

    def cancel_io(self):
        for handle in (self.input_write,self.output_read):
            if handle:self.kernel.CancelIoEx(handle,None)

    def close(self,terminate=False):
        if self.process and terminate and self.isalive():
            self.kernel.TerminateProcess(self.process,1)
            self.kernel.WaitForSingleObject(self.process,1000)
        if self.hpc:
            self.kernel.ClosePseudoConsole(self.hpc);self.hpc=None
        for name in ('input_read','input_write','output_read','output_write','thread','process'):
            handle=getattr(self,name,None)
            if handle:
                self.kernel.CloseHandle(handle);setattr(self,name,None)


class ConPtySession:
    def __init__(self,argv,events,columns=80,rows=24):
        self.events=events
        self.inputs=queue.Queue(maxsize=128)
        self.stop=threading.Event()
        self._size=None
        self.pty=None
        self.job=ProcessJob()
        try:
            self.pty=_ConPty(argv,columns,rows)
            self.pid=self.pty.pid
            if self.pty.isalive():self.job.attach(self.pid)
            self.thread=threading.Thread(target=self._run,daemon=True,name='mathcraft-conpty')
            self.thread.start()
        except Exception:
            self.job.close()
            if self.pty:self.pty.close(terminate=True)
            self.pty=None
            raise

    def write(self,data):
        if self.stop.is_set():raise RuntimeError('Terminal session is closed')
        if not isinstance(data,str):raise TypeError('Terminal input must be text')
        if len(data)>65536:raise ValueError('Input is limited to 65536 characters per write')
        try:self.inputs.put_nowait(data)
        except queue.Full:raise RuntimeError('Terminal input queue is full') from None

    def resize(self,columns,rows):
        if not (2<=columns<=512 and 2<=rows<=256):raise ValueError('Invalid terminal dimensions')
        self._size=(columns,rows)

    def _emit(self,kind,value):
        while not self.stop.is_set():
            try:
                self.events.put((kind,value),timeout=0.05)
                return
            except queue.Full:pass

    def _run(self):
        error=None
        try:
            while not self.stop.is_set():
                size,self._size=self._size,None
                if size:self.pty.resize(*size)
                for _ in range(16):
                    try:value=self.inputs.get_nowait()
                    except queue.Empty:break
                    if self.stop.is_set():break
                    for start in range(0,len(value),4096):self.pty.write(value[start:start+4096])
                data=self.pty.read()
                if data:self._emit('data',data)
                elif not self.pty.isalive():break
                self.stop.wait(0.005)
        except Exception as exc:
            if not self.stop.is_set():error=str(exc)
        finally:self._emit('exit',error)

    def close(self):
        if self.stop.is_set():return
        self.stop.set()
        self.job.close()
        if self.pty:self.pty.cancel_io()
        self.thread.join(timeout=2)
        if self.thread.is_alive():raise RuntimeError('ConPTY worker did not stop')
        if self.pty:self.pty.close()
        self.pty=None
