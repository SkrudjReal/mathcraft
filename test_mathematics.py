import unittest
import mathcraft


class MathematicsTests(unittest.TestCase):
    def test_math_api_exists(self):
        self.assertTrue(hasattr(mathcraft, 'mathematics'))

    def test_standard_functions(self):
        m = mathcraft.mathematics
        self.assertEqual(m.sqrt(81), 9)
        self.assertEqual(m.factorial(6), 720)
        self.assertEqual(m.mean([2, 4, 6]), 4)

    def test_vectors(self):
        m = mathcraft.mathematics
        self.assertEqual(m.dot([1, 2], [3, 4]), 11)
        self.assertEqual(m.cross([1, 0, 0], [0, 1, 0]), [0, 0, 1])
        self.assertAlmostEqual(m.norm([3, 4]), 5)
        with self.assertRaises(ValueError):
            m.dot([1], [1, 2])

    def test_matrices(self):
        m = mathcraft.mathematics
        self.assertEqual(m.matmul([[1, 2]], [[3], [4]]), [[11]])
        self.assertEqual(m.transpose([[1, 2], [3, 4]]), [[1, 3], [2, 4]])
        with self.assertRaises(ValueError):
            m.matmul([[1], [2, 3]], [[1]])

    def test_numerical_methods(self):
        m = mathcraft.mathematics
        self.assertAlmostEqual(m.derivative(lambda x: x*x, 3), 6, places=5)
        self.assertAlmostEqual(m.integrate(lambda x: x*x, 0, 1), 1/3, places=5)
        self.assertAlmostEqual(m.bisect(lambda x: x*x-2, 0, 2), 2**0.5, places=8)
        self.assertAlmostEqual(m.integrate(lambda x: x, 1, 0), -0.5)

    def test_invalid_numerical_parameters(self):
        m = mathcraft.mathematics
        for call in (lambda: m.derivative(lambda x: x, 0, h=0),
                     lambda: m.integrate(lambda x: x, 0, 1, steps=0),
                     lambda: m.bisect(lambda x: x*x+1, -1, 1),
                     lambda: m.bisect(lambda x: float('nan'), 0, 1)):
            with self.assertRaises(ValueError):
                call()
        with self.assertRaises(RuntimeError):
            m.bisect(lambda x: x*x-2, 0, 2, max_iterations=1)


if __name__ == '__main__':
    unittest.main()
