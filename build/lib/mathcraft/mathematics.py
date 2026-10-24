"""Standard-library mathematics plus small real-valued numerical utilities.

math/statistics exports retain their original Python semantics. Numerical
methods are approximations; their tolerances do not bound rounding error.
"""
import math as _math
import statistics as _statistics

# Reuse the installed standard library rather than vendoring copied sources.
__all__ = [name for name in dir(_math) if not name.startswith('_')]
for _name in __all__:
    globals()[_name] = getattr(_math, _name)
for _name in ('mean', 'fmean', 'median', 'mode', 'multimode', 'variance',
              'pvariance', 'stdev', 'pstdev', 'geometric_mean', 'harmonic_mean'):
    globals()[_name] = getattr(_statistics, _name)
    __all__.append(_name)


def dot(a, b):
    """Dot product of equally sized real vectors (including empty vectors)."""
    a, b = tuple(a), tuple(b)
    if len(a) != len(b):
        raise ValueError('Vector dimensions must match')
    return _math.fsum(x * y for x, y in zip(a, b))


def norm(vector):
    """Euclidean norm with scaled accumulation from math.hypot."""
    return _math.hypot(*vector)


def cross(a, b):
    """Cross product of two three-dimensional vectors."""
    a, b = tuple(a), tuple(b)
    if len(a) != 3 or len(b) != 3:
        raise ValueError('Cross product requires two 3D vectors')
    return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2],
            a[0]*b[1]-a[1]*b[0]]


def _matrix(value):
    rows = [list(row) for row in value]
    if not rows or not rows[0] or any(len(row) != len(rows[0]) for row in rows):
        raise ValueError('Matrix must be nonempty and rectangular')
    return rows


def transpose(matrix):
    """Return a new transposed rectangular matrix."""
    return [list(column) for column in zip(*_matrix(matrix))]


def matmul(a, b):
    """Multiply rectangular real matrices; inputs are never modified."""
    a, b = _matrix(a), _matrix(b)
    if len(a[0]) != len(b):
        raise ValueError('Inner matrix dimensions must match')
    columns = list(zip(*b))
    return [[dot(row, column) for column in columns] for row in a]


def _finite(value, name):
    value = float(value)
    if not _math.isfinite(value):
        raise ValueError(name + ' must be finite')
    return value


def _count(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(name + ' must be a positive integer')
    return value


def derivative(function, x, h=1e-5):
    """Central finite difference with a positive absolute step."""
    x, h = _finite(x, 'x'), _finite(h, 'h')
    if h <= 0 or x+h == x or x-h == x:
        raise ValueError('h must be positive and resolvable at x')
    left, right = _finite(x-h, 'left sample'), _finite(x+h, 'right sample')
    result = (_finite(function(right), 'f(x+h)') -
              _finite(function(left), 'f(x-h)')) / (2*h)
    return _finite(result, 'derivative')


def integrate(function, a, b, steps=1000):
    """Composite trapezoidal rule with exactly steps subintervals."""
    a, b = _finite(a, 'a'), _finite(b, 'b')
    steps = _count(steps, 'steps')
    if a == b:
        return 0.0
    width = _finite((b-a)/steps, 'step width')
    if width == 0 or a+width == a:
        raise ValueError('Integration step is too small')
    ends = _finite(function(a), 'f(a)')/2 + _finite(function(b), 'f(b)')/2
    interior = _math.fsum(_finite(function(a+i*width), 'integrand')
                          for i in range(1, steps))
    return _finite(width*(ends+interior), 'integral')


def bisect(function, a, b, tolerance=1e-10, max_iterations=1000):
    """Find a bracketed root of a continuous real function.

    Return when bracket half-width <= tolerance or an exact zero is sampled.
    Raise RuntimeError on iteration exhaustion or insufficient float precision.
    """
    a, b = _finite(a, 'a'), _finite(b, 'b')
    tolerance = _finite(tolerance, 'tolerance')
    max_iterations = _count(max_iterations, 'max_iterations')
    if a >= b or tolerance <= 0:
        raise ValueError('Require a < b and tolerance > 0')
    fa, fb = _finite(function(a), 'f(a)'), _finite(function(b), 'f(b)')
    if fa == 0:
        return a
    if fb == 0:
        return b
    if (fa > 0) == (fb > 0):
        raise ValueError('Endpoints must bracket a root')
    for _ in range(max_iterations):
        midpoint = a/2 + b/2
        fm = _finite(function(midpoint), 'f(midpoint)')
        if fm == 0 or b/2-a/2 <= tolerance:
            return midpoint
        if midpoint == a or midpoint == b:
            raise RuntimeError('Floating-point precision exhausted')
        if (fa > 0) == (fm > 0):
            a, fa = midpoint, fm
        else:
            b = midpoint
    raise RuntimeError('Bisection did not converge within max_iterations')


__all__ += ['dot', 'norm', 'cross', 'transpose', 'matmul', 'derivative',
            'integrate', 'bisect']
