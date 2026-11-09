"""Exact binomial statistics without scipy."""
import math


def _logpmf(i, n, p):
    if p <= 0:
        return 0.0 if i == 0 else -math.inf
    if p >= 1:
        return 0.0 if i == n else -math.inf
    return math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * math.log(p) + (n - i) * math.log1p(-p)


def binom_cdf(k, n, p):
    """P(X <= k)."""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    return min(1.0, sum(math.exp(_logpmf(i, n, p)) for i in range(k + 1)))


def binom_sf_ge(k, n, p):
    """P(X >= k)."""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    return min(1.0, sum(math.exp(_logpmf(i, n, p)) for i in range(k, n + 1)))


def _bisect(f, lo=0.0, hi=1.0, iters=200):
    """Root of a monotone f on [lo, hi] (sign change assumed)."""
    flo = f(lo)
    for _ in range(iters):
        mid = (lo + hi) / 2
        if (f(mid) > 0) == (flo > 0):
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def cp_lower(k, n, alpha):
    """Largest-coverage lower limit: p with P(X >= k | p) = alpha."""
    if k <= 0:
        return 0.0
    return _bisect(lambda p: binom_sf_ge(k, n, p) - alpha)


def cp_upper(k, n, alpha):
    """p with P(X <= k | p) = alpha."""
    if k >= n:
        return 1.0
    return _bisect(lambda p: binom_cdf(k, n, p) - alpha)


def clopper_pearson(k, n, conf=0.95):
    """Two-sided exact interval (lo, hi)."""
    if n <= 0:
        return (None, None)
    a = (1 - conf) / 2
    return (cp_lower(k, n, a), cp_upper(k, n, a))


def upper_one_sided(k, n, conf=0.95):
    """One-sided exact upper limit; for k = 0 this is 1 - (1 - conf)^(1/n)."""
    if n <= 0:
        return None
    return cp_upper(k, n, 1 - conf)


def binom_test_greater(k, n, p0):
    """Exact one-sided p-value P(X >= k | n, p0)."""
    return binom_sf_ge(k, n, p0)


def fisher_exact(a, b, c, d):
    """Two-sided Fisher exact p for [[a, b], [c, d]] by enumerating the hypergeometric table."""
    r1, r2, c1 = a + b, c + d, a + c
    n = r1 + r2
    if n == 0:
        return None
    lo, hi = max(0, c1 - r2), min(r1, c1)

    def prob(x):
        return math.comb(r1, x) * math.comb(r2, c1 - x) / math.comb(n, c1)

    p_obs = prob(a)
    return min(1.0, sum(prob(x) for x in range(lo, hi + 1) if prob(x) <= p_obs * (1 + 1e-7)))
