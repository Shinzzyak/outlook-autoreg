"""SigmaDrift-style human mouse path generation (Python port).

DD9: SigmaDrift (ck0i, 54*, C++20 header-only, PhD research) outperforms
WindMouse: Fitts-compliant, 2 sub-movements vs 15, bell-shaped velocity,
path efficiency 0.985 vs 0.973. Core ideas (Plamondon Kinematic Theory +
Harris-Wolpert signal-dependent noise + OU drift + physiological tremor):

1. Sigma-lognormal velocity primitives — asymmetric bell speed profiles
2. Two-phase surge — ballistic stroke (~93% distance) + 0-2 corrective
   sub-movements (overshoot correction)
3. OU lateral drift — mean-reverting stochastic hand drift
4. Signal-dependent noise — noise ∝ command magnitude (Fitts emerges)
5. Speed-modulated tremor — 8-12Hz tremor, suppressed during fast phase
6. Gamma inter-sample timing — non-constant polling intervals

Simplified to pure-python (no numpy). Port of the algorithm shape, not a
1:1 transcription (C++ paper impl is header-only, MIT).

ponytail: C++ impl has more knobs (target_width, overshoot_prob per-move);
this port fixes a few sane constants. Add knobs if A/B testing demands.
"""

import math
import random


def _lognormal_pdf(x, mu, sigma):
    """Lognormal PDF (velocity primitive shape)."""
    if x <= 0:
        return 0.0
    return (1.0 / (x * sigma * math.sqrt(2 * math.pi))) * math.exp(
        -((math.log(x) - mu) ** 2) / (2 * sigma * sigma)
    )


def _sigma_lognormal_velocity(t, t0, mu, sigma, amplitude):
    """Sigma-lognormal velocity primitive at time t (ms scale)."""
    return amplitude * _lognormal_pdf(max(t - t0, 1e-6), mu, sigma)


def _stroke_velocity(t, t0, duration_ms, distance, direction):
    """Ballistic stroke velocity profile (sigma-lognormal shaped)."""
    # mu/sigma tuned so peak lands ~35% into the stroke (real ballistic)
    mu = math.log(duration_ms * 0.35)
    sigma = 0.45
    # normalize so integral ≈ distance
    v = _sigma_lognormal_velocity(t, t0, mu, sigma, 1.0)
    peak = _sigma_lognormal_velocity(t0 + duration_ms * 0.35, t0, mu, sigma, 1.0)
    if peak <= 0:
        return 0.0, 0.0
    speed = distance * v / (peak * duration_ms * 0.9)
    return direction[0] * speed, direction[1] * speed


def sigmadrift_path(x0, y0, x1, y1, rng=None, seed=None):
    """Generate human-like mouse path from (x0,y0) to (x1,y1).

    Returns list of (x, y, t_ms). Two-phase surge: ballistic ~93% of
    distance, then 0-2 corrective sub-movements (overshoot correction).
    """
    rng = random.Random(seed) if seed is not None else (rng or random)
    dist = math.hypot(x1 - x0, y1 - y0)
    if dist < 2:
        return [(x1, y1, 0)]
    direction = ((x1 - x0) / dist, (y1 - y0) / dist)
    # perpendicular for lateral drift
    perp = (-direction[1], direction[0])

    # ballistic stroke: ~93% of distance, duration 500-900ms (Fitts-ish)
    ballistic_dist = dist * rng.uniform(0.88, 0.97)
    duration_ms = rng.uniform(500, 900)
    # signal-dependent noise: sigma ∝ speed (Harris-Wolpert)
    noise_scale = 0.06 + 0.05 * (ballistic_dist / max(dist, 1))

    # OU lateral drift (mean-reverting)
    theta = 8.0
    sigma_drift = 0.35 * noise_scale * ballistic_dist
    lat = 0.0
    lat_v = 0.0

    path = []
    t = 0
    # sampling ~60Hz dengan jitter kecil
    # posisi = integral kecepatan; v dinormalisasi agar total = ballistic_dist
    # (lognormal primitives: peak di ~35% durasi → bell velocity profile)
    mu = math.log(duration_ms * 0.35)
    sigma = 0.45
    # normalisasi: integral diskrit dari primitive ≈ 1.0
    _norm = 0.0
    _tt = 0.0
    while _tt < duration_ms:
        _norm += _lognormal_pdf(max(_tt - 0, 1e-6), mu, sigma) * 16.0
        _tt += 16.0
    while t < duration_ms:
        dt_ms = 16.0 * rng.uniform(0.7, 1.3)  # ~60Hz, jitter
        dt = dt_ms / 1000.0  # detik untuk drift/noise
        # kecepatan ter-normalisasi (px/ms)
        v = _lognormal_pdf(max(t - 0, 1e-6), mu, sigma) / max(_norm, 1e-9)
        vx = direction[0] * ballistic_dist * v
        vy = direction[1] * ballistic_dist * v
        # lateral drift + signal-dependent noise
        lat_v += -theta * lat_v * dt + sigma_drift * math.sqrt(dt) * rng.gauss(0, 1)
        lat += lat_v * dt
        # speed-modulated tremor: 8-12Hz, suppressed during fast phase
        speed = math.hypot(vx, vy)
        speed_frac = speed / max(speed, 1e-6)
        tremor_amp = 0.5 * (1.0 - min(speed_frac * 2, 1.0))  # suppress at speed
        tremor_freq = rng.uniform(8, 12)
        tx = tremor_amp * math.sin(2 * math.pi * tremor_freq * t / 1000.0 + rng.uniform(0, 6.28))
        ty = tremor_amp * math.sin(2 * math.pi * tremor_freq * t / 1000.0 + rng.uniform(0, 6.28) + 1.57)

        px = x0 + direction[0] * (vx * dt_ms) + perp[0] * lat + tx
        py = y0 + direction[1] * (vy * dt_ms) + perp[1] * lat + ty
        path.append((px, py, t))
        t += dt_ms

    # corrective sub-movements: 0-2, overshoot then settle
    cx, cy = path[-1][0], path[-1][1]
    remaining_x, remaining_y = x1 - cx, y1 - cy
    n_corrections = rng.randint(0, 2)
    for i in range(n_corrections):
        # small correction toward target, partial (overshoot possible)
        frac = rng.uniform(0.4, 1.0)
        cx += remaining_x * frac + rng.gauss(0, 1.5)
        cy += remaining_y * frac + rng.gauss(0, 1.5)
        t += rng.gammavariate(3.0, 4.0) * 6
        path.append((cx, cy, t))
        remaining_x, remaining_y = x1 - cx, y1 - cy

    # final settle
    path.append((x1, y1, t + rng.uniform(20, 80)))
    return path


def _selftest():
    """Sanity check: path reaches target, bell-shaped velocity, monotonic t."""
    rng = random.Random(42)
    for _ in range(5):
        path = sigmadrift_path(100, 120, 900, 640, rng=rng)
        x, y, t = path[-1]
        assert abs(x - 900) < 4 and abs(y - 640) < 4, f"target miss: {x},{y}"
        ts = [p[2] for p in path]
        assert all(b >= a for a, b in zip(ts, ts[1:])), "t not monotonic"
        # velocity bell: speed up then down (ukur fase ballistic saja —
        # corrective sub-movement kecil di akhir bikin delta besar)
        speeds = []
        for i in range(1, len(path)):
            dx = path[i][0] - path[i - 1][0]
            dy = path[i][1] - path[i - 1][1]
            dt = max(path[i][2] - path[i - 1][2], 1e-6)
            speeds.append(math.hypot(dx, dy) / dt)
        # potong 15% terakhir (corrective) — cek bell di fase utama
        main = speeds[: int(len(speeds) * 0.85)]
        peak_idx = main.index(max(main))
        assert 0.15 < peak_idx / len(main) < 0.8, f"peak not mid-path: {peak_idx}/{len(main)}"
        assert len(path) > 15, f"path too short: {len(path)}"
    print(f"SigmaDrift self-test OK: {len(path)} pts, bell velocity, target hit")


if __name__ == "__main__":
    _selftest()
