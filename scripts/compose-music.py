#!/usr/bin/env python3
"""Compose the catalogue's background music: original, procedural, loop-exact.

    ./scripts/compose-music.py --list                      # styles and themes
    ./scripts/compose-music.py --theme "Bloodhouse"        # one theme
    ./scripts/compose-music.py --all --apply               # every recipe, wired in
    ./scripts/compose-music.py --all --preview-dir /tmp/p  # MP3 previews as well

scripts/music-recipes.json says which theme gets which style, in which key, at
what tempo, with which signature sounds. A theme's track is written to
themes/<name>/music.ogg; --apply also points its theme.json at it.

Every note here is synthesised by the code below. Nothing is sampled, looped
from a pack or traced from an existing tune, so the catalogue owns all of it.

WHY THE LOOPS HAVE NO SEAM

The client plays a theme's track with Mix_PlayMusic(music, -1), forever. MP3
cannot loop cleanly - the encoder pads both ends, so each repeat has a gap - but
Ogg Vorbis decodes sample-exact, so a track whose last sample leads into its
first plays as one continuous piece. Two things make that true:

  - Notes still sounding at the end of the loop are folded back onto its start
    (Track.fold), so a chord held over the barline is ringing when the loop
    comes round, as it would be on a second pass.
  - Every static effect - reverb, filters, ambience beds - is CIRCULAR over one
    loop length, done in the frequency domain. Circular is usually the bug in
    FFT filtering; here it is the point. The reverb tail of the last bar lands
    on the first, and the output is periodic by construction.

check_loop() compares the step across the seam with the track's own steps and
refuses to write a file whose loop point would click.

MATCHING THE THEME

A track is written for its theme's art, not picked from a shelf: a style sets
the instruments and the harmony (ominous, festive, cyber, desert...), and the
recipe sets the key, tempo and the sounds that tie it to the picture - a
heartbeat under Bloodhouse, the borb croaking by the fire in Borb's Lair, sleigh bells slowed and detuned under Sinister
Claus, rain under Sengoku Rain, a calliope out of tune under Greasepaint.

Needs numpy and soundfile (pip install numpy soundfile lameenc); soundfile
bundles a libsndfile with Vorbis, lameenc is only for --preview-dir.
"""
import argparse
import json
import os
import sys

import numpy as np

SR = 32000
TAU = 2 * np.pi
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RECIPES = os.path.join(HERE, "music-recipes.json")
TRACK_FILE = "music.ogg"

# ---------------------------------------------------------------------------
# pitch, scales, chords
# ---------------------------------------------------------------------------

NOTE = {n: i for i, n in enumerate("C C# D D# E F F# G G# A A# B".split())}
NOTE.update({"Db": 1, "Eb": 3, "Gb": 6, "Ab": 8, "Bb": 10})

MODES = {
    "major": (0, 2, 4, 5, 7, 9, 11), "minor": (0, 2, 3, 5, 7, 8, 10),
    "dorian": (0, 2, 3, 5, 7, 9, 10), "phrygian": (0, 1, 3, 5, 7, 8, 10),
    "harmonic": (0, 2, 3, 5, 7, 8, 11), "hijaz": (0, 1, 4, 5, 7, 8, 10),
    "mixolydian": (0, 2, 4, 5, 7, 9, 10), "lydian": (0, 2, 4, 6, 7, 9, 11),
    "pent_major": (0, 2, 4, 7, 9), "pent_minor": (0, 3, 5, 7, 10),
    "hirajoshi": (0, 2, 3, 7, 8), "insen": (0, 1, 5, 7, 10),
    "locrian": (0, 1, 3, 5, 6, 8, 10),
}


def hz(midi):
    return 440.0 * 2.0 ** ((np.asarray(midi, dtype=float) - 69.0) / 12.0)


class Key:
    def __init__(self, tonic, mode):
        self.tonic = NOTE[tonic]
        self.mode = mode
        self.steps = MODES[mode]

    def note(self, degree, octave=4):
        n = len(self.steps)
        o, i = divmod(int(degree), n)
        return 12 * (octave + 1 + o) + self.tonic + self.steps[i]

    def chord(self, degree, octave=3, size=3):
        """Stack thirds inside the scale - every other step."""
        return [self.note(degree + 2 * k, octave) for k in range(size)]

    def nearest_degree(self, midi, octave=4):
        best, best_d = 0, 1e9
        for d in range(-14, 22):
            dist = abs(self.note(d, octave) - midi)
            if dist < best_d:
                best, best_d = d, dist
        return best


# ---------------------------------------------------------------------------
# frequency-domain helpers (circular by design - see the module docstring)
# ---------------------------------------------------------------------------

def spectrum_filter(x, lo=None, hi=None, order=2, peaks=()):
    """Zero-phase filter over the whole buffer, in the frequency domain."""
    x = np.asarray(x, dtype=float)
    n = x.shape[0]
    f = np.fft.rfftfreq(n, 1.0 / SR)
    h = np.ones_like(f)
    if hi:
        h *= 1.0 / np.sqrt(1.0 + (f / hi) ** (2 * order))
    if lo:
        h *= 1.0 / np.sqrt(1.0 + (lo / np.maximum(f, 1e-9)) ** (2 * order))
    if peaks:
        p = np.zeros_like(f)
        for centre, gain, width in peaks:
            p += gain * np.exp(-0.5 * ((f - centre) / width) ** 2)
        h *= p
    spec = np.fft.rfft(x, axis=0)
    if x.ndim == 2:
        h = h[:, None]
    return np.fft.irfft(spec * h, n=n, axis=0)


def oneshot_filter(x, **kw):
    """spectrum_filter for a single note: zero-padded so its end cannot wrap."""
    n = x.shape[0]
    pad = np.zeros_like(x)
    return spectrum_filter(np.concatenate([x, pad]), **kw)[:n]


def circular_convolve(x, ir):
    n = x.shape[0]
    pad = np.zeros((n, 2))
    k = min(ir.shape[0], n)
    pad[:k] = ir[:k]
    return np.fft.irfft(np.fft.rfft(x, axis=0) * np.fft.rfft(pad, axis=0), n=n, axis=0)


_IR_CACHE = {}


def reverb_ir(seconds=2.6, damping=0.55, predelay=0.02):
    key = (round(seconds, 2), damping)
    if key in _IR_CACHE:
        return _IR_CACHE[key]
    rng = np.random.default_rng(7)
    k = int(seconds * 1.6 * SR)
    t = np.arange(k) / SR
    ir = np.zeros((k, 2))
    for ch in range(2):
        noise = rng.standard_normal(k)
        low = spectrum_filter(noise, hi=600)
        mid = spectrum_filter(noise, lo=600, hi=3500)
        high = spectrum_filter(noise, lo=3500)
        tail = (low * np.exp(-6.91 * t / seconds)
                + mid * np.exp(-6.91 * t / (seconds * (1 - 0.35 * damping)))
                + high * np.exp(-6.91 * t / (seconds * (1 - 0.75 * damping))))
        ir[:, ch] = tail * np.clip((t - predelay) / 0.03, 0, 1)
        for d, g in ((0.007, 0.5), (0.013, 0.35), (0.021, 0.3), (0.029, 0.22)):
            i = int((predelay + d + 0.002 * ch) * SR)
            ir[i, ch] += g * (1 if rng.random() > 0.5 else -1) * 6
    ir /= np.sqrt(np.sum(ir ** 2) / 2)
    _IR_CACHE[key] = ir
    return ir


# ---------------------------------------------------------------------------
# oscillators and envelopes
# ---------------------------------------------------------------------------

_TABLE_N = 4096


def wavetable(amps, phases=None):
    x = np.arange(_TABLE_N) / _TABLE_N
    tbl = np.zeros(_TABLE_N)
    for k, a in enumerate(amps, start=1):
        if a:
            ph = 0.0 if phases is None else phases[k - 1]
            tbl += a * np.sin(TAU * k * x + ph)
    return tbl


def osc(freq, n, amps, phase0=0.0, phases=None):
    """Band-limited oscillator: a wavetable read with a phase accumulator.

    freq is a scalar or a per-sample array (vibrato, glides). amps[k-1] is the
    amplitude of harmonic k; harmonics above 13 kHz at the highest frequency
    reached are dropped so nothing folds back.
    """
    f = np.broadcast_to(np.asarray(freq, dtype=float), (n,))
    fmax = float(f.max()) if n else 1.0
    kmax = max(1, int(13000 / max(fmax, 1.0)))
    tbl = wavetable(list(amps)[:kmax], phases)
    ph = (phase0 + np.cumsum(f) / SR) % 1.0
    idx = ph * _TABLE_N
    i0 = idx.astype(int) % _TABLE_N
    frac = idx - np.floor(idx)
    return tbl[i0] * (1 - frac) + tbl[(i0 + 1) % _TABLE_N] * frac


SAW = [1 / k for k in range(1, 81)]
SOFT_SAW = [1 / k ** 1.6 for k in range(1, 81)]
SQUARE = [(1 / k if k % 2 else 0) for k in range(1, 81)]
TRIANGLE = [((-1) ** ((k - 1) // 2) / k ** 2 if k % 2 else 0) for k in range(1, 81)]
SINE = [1.0]


def pulse(duty):
    return [2 * np.sin(np.pi * k * duty) / (np.pi * k) for k in range(1, 81)]


def env(n_hold, attack, release, n=None, decay=None, sustain=1.0):
    """Attack, optional exponential decay towards sustain, hold, cosine release."""
    a = max(int(attack * SR), 1)
    r = max(int(release * SR), 1)
    n = n if n is not None else n_hold + r
    e = np.ones(n)
    k = min(a, n)
    e[:k] = 0.5 - 0.5 * np.cos(np.pi * np.arange(k) / a)
    if decay:
        t = np.arange(n) / SR
        e *= sustain + (1 - sustain) * np.exp(-t / decay)
    if n_hold < n:
        rel = np.zeros(n - n_hold)
        k = min(r, n - n_hold)
        rel[:k] = 0.5 + 0.5 * np.cos(np.pi * np.arange(k) / r)
        e[n_hold:] *= rel
    return e


def vibrato(n, rate=5.5, depth=0.003, delay=0.25, seed=0):
    t = np.arange(n) / SR
    rng = np.random.default_rng(seed)
    return 1 + depth * np.sin(TAU * rate * t + rng.uniform(0, TAU)) * np.clip((t - delay) / 0.4, 0, 1)


def crush(x, bits=6, hold=3):
    q = 2 ** bits
    y = np.round(x * q) / q
    return np.repeat(y[::hold], hold)[: x.shape[0]]


# ---------------------------------------------------------------------------
# instruments - each returns a mono (n,) or stereo (n,2) array
# ---------------------------------------------------------------------------

def _hold(dur, release):
    n_hold = int(dur * SR)
    return n_hold, n_hold + int(release * SR)


def pad(note, dur, attack=1.2, release=1.8, bright=0.4, detune=7.0, vib=0.0, seed=0):
    """Three detuned saws spread left, centre, right."""
    f0 = float(hz(note))
    n_hold, n = _hold(dur, release)
    out = np.zeros((n, 2))
    roll = 2.3 - 1.4 * bright
    amps = [1 / k ** roll for k in range(1, 81)]
    rng = np.random.default_rng(seed)
    for i, (cents, pan) in enumerate(((-detune, 0.15), (0.0, 0.5), (detune, 0.85))):
        f = f0 * 2 ** (cents / 1200)
        if vib:
            f = f * vibrato(n, rate=5.0 + i * 0.4, depth=vib, seed=seed + i)
        v = osc(f, n, amps, phase0=rng.random())
        out[:, 0] += v * np.cos(pan * np.pi / 2)
        out[:, 1] += v * np.sin(pan * np.pi / 2)
    return out * env(n_hold, attack, release, n)[:, None] * 0.17


def strings(note, dur, attack=0.5, release=0.9, bright=0.55, seed=0):
    return pad(note, dur, attack=attack, release=release, bright=bright, detune=9, vib=0.004, seed=seed)


def choir(note, dur, vowel="ah", attack=1.0, release=1.6, seed=0):
    formants = {"ah": [(700, 1.0, 110), (1150, 0.55, 140), (2800, 0.22, 240)],
                "oo": [(320, 1.0, 80), (800, 0.4, 120), (2400, 0.1, 220)],
                "eh": [(530, 1.0, 90), (1850, 0.5, 160), (2500, 0.25, 220)]}[vowel]
    v = pad(note, dur, attack=attack, release=release, bright=0.95, detune=13, vib=0.006, seed=seed)
    return oneshot_filter(v, peaks=formants) * 2.4


def organ(note, dur, attack=0.06, release=0.5, drawbars=(1, 0.6, 0.35, 0.25, 0.12, 0.1), trem=0.05):
    f0 = float(hz(note))
    n_hold, n = _hold(dur, release)
    amps = [0.0] * 8
    for h, g in zip((1, 2, 3, 4, 6, 8), drawbars):
        amps[h - 1] = g
    t = np.arange(n) / SR
    s = osc(f0, n, amps) * (1 + trem * np.sin(TAU * 5.4 * t))
    return s * env(n_hold, attack, release, n) * 0.12


def calliope(note, dur, warp=0.0, seed=0):
    """Steam-organ pipes; warp bends each note flat and wobbles it."""
    rng = np.random.default_rng(seed)
    f0 = float(hz(note)) * 2 ** (rng.normal(0, 18 * warp) / 1200)
    n_hold, n = _hold(dur, 0.12)
    t = np.arange(n) / SR
    f = f0 * (1 + 0.012 * warp * np.sin(TAU * 3.1 * t) - 0.02 * warp * np.clip(t / 1.5, 0, 1))
    f = f * vibrato(n, rate=6.5, depth=0.006, delay=0.05, seed=seed)
    s = osc(f, n, [1, 0.45, 0.18, 0.1, 0.04])
    return s * env(n_hold, 0.02, 0.12, n) * 0.15


def epiano(note, dur, vel=0.8, release=0.5):
    f = float(hz(note))
    n_hold, n = _hold(dur, release)
    t = np.arange(n) / SR
    index = (1.6 * vel) * np.exp(-t / 0.35) + 0.25
    body = np.sin(TAU * f * t + index * np.sin(TAU * f * t))
    tine = 0.25 * vel * np.sin(TAU * f * 14 * t) * np.exp(-t / 0.03)
    amp = np.exp(-t / (1.8 if f < 400 else 1.1))
    return (body + tine) * amp * env(n_hold, 0.004, release, n) * 0.22 * vel


def bell(note, dur=3.0, vel=0.7, ratio=3.5, decay=2.2, detune_cents=0.0):
    f = float(hz(note)) * 2 ** (detune_cents / 1200)
    n = int(dur * SR)
    t = np.arange(n) / SR
    index = 2.4 * vel * np.exp(-t / 0.6)
    s = np.sin(TAU * f * t + index * np.sin(TAU * f * ratio * t))
    return s * np.exp(-t / decay) * env(n, 0.002, 0.05, n) * 0.2 * vel


def musicbox(note, vel=0.7, detune_cents=0.0, decay=0.9):
    f = float(hz(note)) * 2 ** (detune_cents / 1200)
    n = int(2.4 * SR)
    t = np.arange(n) / SR
    s = (np.sin(TAU * f * t) * np.exp(-t / decay)
         + 0.35 * np.sin(TAU * f * 2.0 * t) * np.exp(-t / (decay * 0.4))
         + 0.18 * np.sin(TAU * f * 5.4 * t) * np.exp(-t / 0.08))
    return s * env(n, 0.001, 0.05, n) * 0.2 * vel


def glock(note, vel=0.7):
    f = float(hz(note))
    n = int(2.0 * SR)
    t = np.arange(n) / SR
    s = (np.sin(TAU * f * t) * np.exp(-t / 1.0) + 0.4 * np.sin(TAU * f * 2.76 * t) * np.exp(-t / 0.3)
         + 0.2 * np.sin(TAU * f * 5.4 * t) * np.exp(-t / 0.1))
    return s * env(n, 0.001, 0.05, n) * 0.18 * vel


def marimba(note, vel=0.7, bright=0.4):
    f = float(hz(note))
    n = int(1.4 * SR)
    t = np.arange(n) / SR
    s = (np.sin(TAU * f * t) * np.exp(-t / 0.45) + bright * np.sin(TAU * f * 3.93 * t) * np.exp(-t / 0.08)
         + 0.5 * bright * np.sin(TAU * f * 9.2 * t) * np.exp(-t / 0.02))
    return s * env(n, 0.001, 0.05, n) * 0.25 * vel


def pluck(note, dur=1.6, vel=0.8, brightness=0.6, decay=1.0, bend=0.0):
    """Plucked string as additive partials; the high ones die first.

    bend > 0 starts the note sharp and settles it, the way a hard pluck on a
    koto or an oud does. Additive rather than Karplus-Strong, which rounds the
    delay to whole samples and plays the top of the range out of tune.
    """
    f = float(hz(note))
    n = int(dur * SR)
    t = np.arange(n) / SR
    fcurve = 1 + bend * np.exp(-t / 0.05)
    phase = np.cumsum(f * fcurve) / SR
    out = np.zeros(n)
    for k in range(1, int(min(16, 9000 / f)) + 1):
        damp = (1.2 + (0.9 - 0.6 * brightness) * k ** 1.6) / decay
        out += np.sin(TAU * k * phase * (1 + 0.0004 * k * k)) / k * np.exp(-t * damp)
    return out * env(n, 0.002, 0.08, n) * 0.25 * vel


def strum(notes, dur=1.8, vel=0.7, spread=0.018, brightness=0.55, down=True):
    order = notes if down else notes[::-1]
    n = int((dur + spread * len(notes)) * SR)
    out = np.zeros(n)
    for i, nt in enumerate(order):
        p = pluck(nt, dur, vel * (0.85 + 0.15 * (i == 0)), brightness=brightness)
        o = int(i * spread * SR)
        out[o:o + p.shape[0]] += p[: n - o]
    return out * 0.6


def flute(note, dur, vel=0.6, release=0.25, breath=0.12, scoop=0.0, seed=0):
    """Breathy flute; scoop > 0 starts flat and bends up (shakuhachi)."""
    f = float(hz(note))
    rng = np.random.default_rng(seed)
    n_hold, n = _hold(dur, release)
    t = np.arange(n) / SR
    fc = f * vibrato(n, rate=5.0, depth=0.005, delay=0.35, seed=seed) * (1 - scoop * np.exp(-t / 0.12))
    tone = osc(fc, n, [1, 0.18, 0.06])
    air = oneshot_filter(rng.standard_normal(n), lo=f * 0.9, hi=f * 4.0) * breath
    chiff = oneshot_filter(rng.standard_normal(n), lo=2000, hi=8000) * np.exp(-t / 0.03) * 0.15
    return (tone + air + chiff) * env(n_hold, 0.06, release, n) * 0.16 * vel


def theremin_line(events, beat_sec, vel=0.6):
    """One continuous voice gliding between notes. events: [(beat, midi, beats)]."""
    end = max(b + d for b, _, d in events) * beat_sec + 0.6
    n = int(end * SR)
    t = np.arange(n) / SR
    f = np.zeros(n)
    amp = np.zeros(n)
    for b, note, d in events:
        i0, i1 = int(b * beat_sec * SR), int((b + d) * beat_sec * SR)
        f[i0:] = float(hz(note))
        amp[i0:i1] = 1
    glide = int(0.09 * SR)
    kernel = np.ones(glide) / glide
    f = np.convolve(np.r_[np.full(glide, f[0]), f], kernel, mode="same")[glide:]
    amp = np.convolve(amp, np.ones(int(0.08 * SR)) / int(0.08 * SR), mode="same")
    f = f * (1 + 0.012 * np.sin(TAU * 6.2 * t))
    return osc(f, n, [1, 0.12, 0.03]) * amp * 0.15 * vel


def brass(note, dur, vel=0.7, release=0.3, seed=0):
    """Saw that brightens through its attack - a filter sweep, approximated."""
    f = float(hz(note)) * vibrato(int((dur + release) * SR), rate=5.2, depth=0.003, delay=0.4, seed=seed)
    n_hold, n = _hold(dur, release)
    dark = osc(f, n, SOFT_SAW)
    bright = osc(f, n, SAW, phase0=0.0)
    t = np.arange(n) / SR
    open_ = np.clip(t / 0.12, 0, 1) * (0.55 + 0.45 * np.exp(-t / 0.5))
    s = dark * (1 - open_) + bright * open_
    return s * env(n_hold, 0.04, release, n) * 0.11 * vel


def reed(note, dur, vel=0.7, release=0.15, musette=0.0, staccato=False):
    """Accordion or bassoon: a nasal reed. musette detunes a second voice."""
    f = float(hz(note))
    n_hold, n = _hold(dur * (0.55 if staccato else 1.0), release)
    amps = [1.0, 0.8, 0.65, 0.5, 0.42, 0.3, 0.22, 0.16, 0.1, 0.06]
    s = osc(f, n, amps)
    if musette:
        s = s + osc(f * 2 ** (musette / 1200), n, amps, phase0=0.3)
    t = np.arange(n) / SR
    s *= 1 + 0.08 * np.sin(TAU * 1.6 * t)
    return s * env(n_hold, 0.03, release, n) * 0.07 * vel


def chip(note, dur, vel=0.6, duty=0.25, release=0.04, slide=0.0):
    f = float(hz(note))
    n_hold, n = _hold(dur, release)
    t = np.arange(n) / SR
    fcurve = f * (1 + slide * np.exp(-t / 0.03))
    return osc(fcurve, n, pulse(duty)) * env(n_hold, 0.003, release, n, decay=0.25, sustain=0.7) * 0.09 * vel


def chip_tri(note, dur, vel=0.8):
    n_hold, n = _hold(dur, 0.03)
    return osc(float(hz(note)), n, TRIANGLE) * env(n_hold, 0.002, 0.03, n) * 0.3 * vel


def chip_noise(vel=0.5, length=0.06, seed=0, tone=8):
    rng = np.random.default_rng(seed)
    n = int(length * SR)
    s = np.repeat(rng.uniform(-1, 1, n // tone + 1), tone)[:n]
    return s * np.exp(-np.arange(n) / (length * 0.4 * SR)) * 0.2 * vel


def saw_lead(note, dur, vel=0.6, release=0.2, bright=0.7, detune=9):
    f = float(hz(note))
    n_hold, n = _hold(dur, release)
    amps = [1 / k ** (1.0 + (1 - bright)) for k in range(1, 81)]
    s = osc(f, n, amps) + 0.7 * osc(f * 2 ** (detune / 1200), n, amps, phase0=0.37)
    return s * env(n_hold, 0.01, release, n, decay=0.4, sustain=0.6) * 0.08 * vel


def fm_bass(note, dur, vel=0.8, ratio=1.0, bite=2.5, release=0.08):
    f = float(hz(note))
    n_hold, n = _hold(dur, release)
    t = np.arange(n) / SR
    index = bite * np.exp(-t / 0.08) + 0.4
    s = np.sin(TAU * f * t + index * np.sin(TAU * f * ratio * t))
    return np.tanh(1.3 * s) * env(n_hold, 0.004, release, n) * 0.3 * vel


def bass(note, dur, vel=0.8, release=0.12):
    f = float(hz(note))
    n_hold, n = _hold(dur, release)
    t = np.arange(n) / SR
    s = np.sin(TAU * f * t) + 0.25 * np.sin(TAU * 2 * f * t) + 0.08 * np.sin(TAU * 3 * f * t)
    return np.tanh(1.4 * s) * env(n_hold, 0.008, release, n, decay=0.6, sustain=0.75) * 0.32 * vel


def drone(note, dur, bright=0.15, seed=0):
    return pad(note, dur, attack=2.5, release=3.0, bright=bright, detune=4, seed=seed)


# --- percussion -------------------------------------------------------------

def kick(vel=0.8, tight=False):
    n = int(0.5 * SR)
    t = np.arange(n) / SR
    freq = 46 + 74 * np.exp(-t / (0.025 if tight else 0.035))
    s = np.sin(TAU * np.cumsum(freq) / SR) * np.exp(-t / (0.16 if tight else 0.22))
    return s * env(n, 0.001, 0.05, n) * 0.55 * vel


def heartbeat(vel=0.8):
    n = int(0.6 * SR)
    out = np.zeros(n)
    for off, g in ((0, 1.0), (0.24, 0.65)):
        k = kick(g)
        k = oneshot_filter(k, hi=180)
        i = int(off * SR)
        out[i:i + k.shape[0]] += k[: n - i]
    return out * 1.6 * vel


def snare(vel=0.6, seed=0, march=False):
    rng = np.random.default_rng(seed)
    n = int(0.35 * SR)
    t = np.arange(n) / SR
    tone = np.sin(TAU * (200 if march else 185) * t) * np.exp(-t / 0.05)
    noise = oneshot_filter(rng.standard_normal(n), lo=900, hi=7000) * np.exp(-t / (0.12 if march else 0.09))
    return (0.5 * tone + 0.7 * noise) * 0.3 * vel


def rim(vel=0.6, seed=0):
    rng = np.random.default_rng(seed)
    n = int(0.18 * SR)
    t = np.arange(n) / SR
    tone = np.sin(TAU * 380 * t) * np.exp(-t / 0.018)
    noise = oneshot_filter(rng.standard_normal(n), lo=1200, hi=5000) * np.exp(-t / 0.03)
    return (0.7 * tone + 0.5 * noise) * 0.28 * vel


def hat(vel=0.5, open_=False, seed=0):
    rng = np.random.default_rng(seed)
    n = int((0.35 if open_ else 0.08) * SR)
    t = np.arange(n) / SR
    noise = oneshot_filter(rng.standard_normal(n), lo=6500, hi=12000)
    return noise * np.exp(-t / (0.12 if open_ else 0.022)) * 0.16 * vel


def clap(vel=0.6, seed=0):
    rng = np.random.default_rng(seed)
    n = int(0.3 * SR)
    t = np.arange(n) / SR
    e = np.zeros(n)
    for off in (0, 0.011, 0.023):
        i = int(off * SR)
        e[i:] += np.exp(-(t[: n - i]) / (0.008 if off < 0.02 else 0.07))
    return oneshot_filter(rng.standard_normal(n), lo=800, hi=6000) * e * 0.18 * vel


def shaker(vel=0.4, seed=0):
    rng = np.random.default_rng(seed)
    n = int(0.12 * SR)
    t = np.arange(n) / SR
    noise = oneshot_filter(rng.standard_normal(n), lo=4000, hi=10000)
    return noise * np.clip(t / 0.02, 0, 1) * np.exp(-t / 0.04) * 0.12 * vel


def woodblock(vel=0.6, pitch=1.0):
    n = int(0.15 * SR)
    t = np.arange(n) / SR
    s = np.sin(TAU * 900 * pitch * t) + 0.5 * np.sin(TAU * 2600 * pitch * t)
    return s * np.exp(-t / 0.025) * 0.18 * vel


def timpani(note, vel=0.8):
    f = float(hz(note))
    n = int(2.2 * SR)
    t = np.arange(n) / SR
    fc = f * (1 + 0.03 * np.exp(-t / 0.08))
    ph = np.cumsum(fc) / SR
    s = (np.sin(TAU * ph) * np.exp(-t / 0.9) + 0.4 * np.sin(TAU * 1.5 * ph) * np.exp(-t / 0.5)
         + 0.25 * np.sin(TAU * 1.99 * ph) * np.exp(-t / 0.35))
    noise = oneshot_filter(np.random.default_rng(int(f)).standard_normal(n), lo=200, hi=2000) * np.exp(-t / 0.03)
    return (s + 0.3 * noise) * 0.4 * vel


def taiko(vel=0.8, pitch=1.0, seed=0):
    n = int(1.2 * SR)
    t = np.arange(n) / SR
    f = (62 + 60 * np.exp(-t / 0.05)) * pitch
    body = np.sin(TAU * np.cumsum(f) / SR) * np.exp(-t / 0.45)
    slap = oneshot_filter(np.random.default_rng(seed).standard_normal(n), lo=300, hi=3000) * np.exp(-t / 0.02)
    return (body + 0.35 * slap) * 0.6 * vel


def frame_drum(vel=0.7, seed=0):
    return oneshot_filter(taiko(vel, pitch=1.35, seed=seed), hi=900) * 0.9


def darbuka(stroke="doum", vel=0.6, seed=0):
    rng = np.random.default_rng(seed)
    n = int(0.4 * SR)
    t = np.arange(n) / SR
    if stroke == "doum":
        s = np.sin(TAU * np.cumsum(110 + 40 * np.exp(-t / 0.02)) / SR) * np.exp(-t / 0.18)
    else:
        s = oneshot_filter(rng.standard_normal(n), lo=1500, hi=7000) * np.exp(-t / 0.025) \
            + 0.4 * np.sin(TAU * 620 * t) * np.exp(-t / 0.03)
    return s * 0.4 * vel


def sleigh_bells(vel=0.6, seed=0, slow=1.0):
    """A shake: a cluster of jingles, each a bright inharmonic ping."""
    rng = np.random.default_rng(seed)
    n = int(0.45 * slow * SR)
    out = np.zeros(n)
    for _ in range(9):
        i = int(rng.uniform(0, 0.05 * slow) * SR)
        m = int(0.18 * slow * SR)
        t = np.arange(m) / SR
        f = rng.uniform(4800, 7800) / slow
        ping = sum(np.sin(TAU * f * r * t) * w for r, w in ((1, 1), (1.47, 0.6), (2.09, 0.35)))
        ping = ping * np.exp(-t / (0.05 * slow)) * rng.uniform(0.5, 1)
        out[i:i + m] += ping[: n - i]
    hiss = oneshot_filter(rng.standard_normal(n), lo=6000, hi=12000) * np.exp(-np.arange(n) / (0.06 * slow * SR))
    return (out * 0.04 + hiss * 0.05) * vel


def boom(vel=0.8, seed=0):
    n = int(3.0 * SR)
    t = np.arange(n) / SR
    f = 34 + 40 * np.exp(-t / 0.15)
    body = np.sin(TAU * np.cumsum(f) / SR) * np.exp(-t / 1.1)
    rumble = oneshot_filter(np.random.default_rng(seed).standard_normal(n), hi=160) * np.exp(-t / 0.8)
    return (body + 0.6 * rumble) * 0.55 * vel


def scrape(note, dur=3.0, vel=0.5, seed=0):
    """Bowed metal: inharmonic FM with a slow, uneasy tremolo."""
    f = float(hz(note))
    n = int(dur * SR)
    t = np.arange(n) / SR
    rng = np.random.default_rng(seed)
    s = np.sin(TAU * f * t + 3.0 * np.sin(TAU * f * 1.414 * t) + 0.5 * np.sin(TAU * 0.7 * t))
    s *= 1 + 0.3 * np.sin(TAU * rng.uniform(3, 7) * t)
    return s * env(n - int(0.8 * SR), 0.9, 0.8, n) * 0.07 * vel


def riser(dur=2.0, vel=0.5, seed=0):
    """Reversed swell into a downbeat - place it so it ends on the hit."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    noise = oneshot_filter(np.random.default_rng(seed).standard_normal(n), lo=300, hi=5000)
    return noise * (t / dur) ** 3 * 0.12 * vel


def chirp(base_note, notes=3, vel=0.5, seed=0):
    rng = np.random.default_rng(seed)
    pieces = []
    for _ in range(notes):
        d = rng.uniform(0.05, 0.09)
        n = int(d * SR)
        t = np.arange(n) / SR
        f0 = float(hz(base_note + rng.choice([0, 2, 4, 7])))
        f = f0 * (1 + 0.35 * (t / d) ** 0.7)
        pieces.append(np.sin(TAU * np.cumsum(f) / SR) * np.sin(np.pi * t / d) ** 2)
        pieces.append(np.zeros(int(rng.uniform(0.03, 0.07) * SR)))
    return np.concatenate(pieces) * 0.12 * vel


def croak(vel=0.5, seed=0, pitch=1.0):
    """The borb: a small, round two-part croak - "rib-bit" - from a pulsing
    throat tone shaped by a mouth formant. The client's own icon is a red,
    spotted frog-ish creature, so the lair gets a croak, not birdsong."""
    rng = np.random.default_rng(seed)
    pieces = []
    for syl, (length, base) in enumerate(((rng.uniform(0.09, 0.13), 1.0), (rng.uniform(0.12, 0.17), 1.18))):
        n = int(length * SR)
        t = np.arange(n) / SR
        f0 = 310 * pitch * base * (1 - 0.08 * t / length)
        tone = osc(f0, n, [1, 0.6, 0.45, 0.3, 0.2, 0.12])
        pulses = 0.5 + 0.5 * np.sign(np.sin(TAU * rng.uniform(26, 34) * t))
        e = np.sin(np.pi * t / length) ** 0.7
        pieces.append(oneshot_filter(tone * pulses * e, peaks=[(900 * pitch, 1.0, 260), (2100 * pitch, 0.35, 400)]))
        pieces.append(np.zeros(int(rng.uniform(0.05, 0.08) * SR)))
    return np.concatenate(pieces) * 0.55 * vel


def blip(note, vel=0.5, length=0.12, rise=1.4):
    n = int(length * SR)
    t = np.arange(n) / SR
    f = float(hz(note)) * (1 + rise * (t / length) ** 2)
    return np.sin(TAU * np.cumsum(f) / SR) * np.sin(np.pi * t / length) * 0.12 * vel


def beep(note, length=0.06, vel=0.5, square=False):
    n = int(length * SR)
    s = osc(float(hz(note)), n, SQUARE[:9] if square else SINE)
    return s * env(n - int(0.01 * SR), 0.002, 0.01, n) * 0.1 * vel


def skitter(vel=0.5, seed=0):
    """Many legs: a quick, uneven run of tiny clicks."""
    rng = np.random.default_rng(seed)
    n = int(0.45 * SR)
    out = np.zeros(n)
    for _ in range(rng.integers(8, 16)):
        i = int(rng.uniform(0, 0.4) * SR)
        m = int(0.004 * SR)
        c = oneshot_filter(rng.standard_normal(m), lo=2000, hi=7000) * np.exp(-np.arange(m) / (0.001 * SR))
        out[i:i + m] += c[: n - i] * rng.uniform(0.4, 1)
    return out * 0.5 * vel


def whisper(dur=0.8, vel=0.5, seed=0):
    """Breath shaped into syllables: no words, only the sound of talk."""
    rng = np.random.default_rng(seed)
    n = int(dur * SR)
    t = np.arange(n) / SR
    sylls = np.zeros(n)
    pos = 0.0
    while pos < dur - 0.08:
        L = rng.uniform(0.08, 0.2)
        i0, i1 = int(pos * SR), int(min(pos + L, dur) * SR)
        sylls[i0:i1] = np.sin(np.pi * (t[i0:i1] - pos) / L) ** 2
        pos += L + rng.uniform(0.02, 0.1)
    vowels = [[(600, 1, 120), (2400, 0.6, 300)], [(400, 1, 90), (2000, 0.7, 250)],
              [(800, 1, 140), (1300, 0.6, 180), (3000, 0.5, 300)]]
    vowel = vowels[int(rng.integers(len(vowels)))]
    noise = oneshot_filter(rng.standard_normal(n), peaks=vowel)
    return noise * sylls * 0.09 * vel


def cricket(vel=0.4, seed=0):
    rng = np.random.default_rng(seed)
    n = int(0.25 * SR)
    out = np.zeros(n)
    f = rng.uniform(4200, 4800)
    for k in range(3):
        i = int(k * 0.045 * SR)
        m = int(0.03 * SR)
        t = np.arange(m) / SR
        out[i:i + m] += np.sin(TAU * f * t) * np.sin(np.pi * t / 0.03) ** 2
    return out * 0.05 * vel


def clank(vel=0.5, seed=0):
    """A pot or a pan: struck metal, short and inharmonic."""
    rng = np.random.default_rng(seed)
    n = int(0.6 * SR)
    t = np.arange(n) / SR
    f = rng.uniform(380, 900)
    s = sum(np.sin(TAU * f * r * t) * np.exp(-t / d) for r, d in ((1, 0.25), (2.31, 0.15), (3.9, 0.08), (5.6, 0.05)))
    return s * 0.06 * vel


# ---------------------------------------------------------------------------
# beds: full-loop ambience, circular so they close on themselves
# ---------------------------------------------------------------------------

def loop_noise(L, seed, lo=None, hi=None, gain=1.0):
    rng = np.random.default_rng(seed)
    return spectrum_filter(rng.standard_normal((L, 2)), lo=lo, hi=hi) * gain


def lfo(L, cycles, phase=0.0):
    """A whole number of cycles per loop, so it closes."""
    return np.sin(TAU * cycles * np.arange(L) / L + phase)


def wind_bed(L, seed=0, gain=0.4):
    a = loop_noise(L, seed, lo=200, hi=900) * (0.55 + 0.45 * lfo(L, 3))[:, None]
    b = loop_noise(L, seed + 1, lo=500, hi=1800) * (0.5 + 0.5 * lfo(L, 2, 1.7))[:, None]
    return (a + 0.6 * b) * gain


def rain_bed(L, seed=0, gain=0.3):
    hiss = loop_noise(L, seed, lo=1500, hi=9000) * 0.5
    rng = np.random.default_rng(seed + 5)
    drops = np.zeros((L, 2))
    for _ in range(int(L / SR * 40)):
        i = int(rng.integers(0, L))
        m = int(0.02 * SR)
        t = np.arange(m) / SR
        d = np.sin(TAU * rng.uniform(2000, 5000) * t) * np.exp(-t / 0.004) * rng.uniform(0.2, 1)
        ch = rng.integers(0, 2)
        k = min(m, L - i)
        drops[i:i + k, ch] += d[:k]
    return (hiss + drops * 0.6) * gain


def stream_bed(L, seed=0, gain=0.3):
    base = loop_noise(L, seed, lo=400, hi=3500)
    flutter = sum(lfo(L, c, seed + c) for c in (37, 53, 71, 97)) / 4
    return base * (0.7 + 0.3 * flutter)[:, None] * gain


def surf_bed(L, cycles, seed=0, gain=0.4):
    wash = loop_noise(L, seed, lo=150, hi=2500)
    return wash * (0.25 + 0.75 * (0.5 + 0.5 * lfo(L, cycles)) ** 2)[:, None] * gain


def fire_bed(L, seed=0, gain=0.25):
    rumble = loop_noise(L, seed, lo=40, hi=260) * (0.8 + 0.2 * lfo(L, 3))[:, None]
    rng = np.random.default_rng(seed + 9)
    pops = np.zeros((L, 2))
    for _ in range(int(L / SR * 7)):
        i = int(rng.integers(0, L))
        m = int(0.012 * SR)
        p = oneshot_filter(rng.standard_normal(m), lo=1500, hi=9000) * np.exp(-np.arange(m) / (0.002 * SR))
        p *= rng.pareto(2.5) * 1.2
        k = min(m, L - i)
        pan = rng.uniform(0.2, 0.8)
        pops[i:i + k, 0] += p[:k] * np.cos(pan * np.pi / 2)
        pops[i:i + k, 1] += p[:k] * np.sin(pan * np.pi / 2)
    return (rumble * 0.5 + pops) * gain


def hum_bed(L, seed=0, gain=0.15, base=55.0):
    t = np.arange(L) / SR
    cycles = round(base * L / SR)
    f = cycles * SR / L                       # a whole number of cycles per loop
    s = np.sin(TAU * f * t) + 0.3 * np.sin(TAU * 2 * f * t) + 0.1 * np.sin(TAU * 3 * f * t)
    air = loop_noise(L, seed, lo=2000, hi=6000) * 0.05
    return (np.stack([s, s], 1) * 0.3 + air) * gain


# ---------------------------------------------------------------------------
# melody: a motif, its answer, a contrast, a return - inside the chords
# ---------------------------------------------------------------------------

RHYTHMS = {
    4: [[1, 1, 2, 1, 0.5, 0.5, 2], [1.5, 0.5, 1, 1, 2, 2], [0.5, 0.5, 1, 1, 1, 3, 1],
        [2, 1, 1, 1.5, 0.5, 2], [1, 0.5, 0.5, 1, 1, 1, 1, 2], [1.5, 1.5, 1, 1, 1, 2]],
    3: [[2, 1, 1, 1, 1], [1, 1, 1, 2, 1], [1.5, 0.5, 1, 3], [1, 2, 1, 1, 1], [2, 1, 3]],
    6: [[2, 1, 2, 1, 3, 3], [3, 2, 1, 3, 3], [1, 1, 1, 2, 1, 3, 3], [2, 1, 3, 2, 1, 3]],
}
SPARSE = {4: [[2, 2, 4], [3, 1, 4], [4, 2, 2]], 3: [[3, 3], [2, 1, 3]], 6: [[3, 3, 6], [6, 3, 3]]}
BUSY = {4: [[0.5] * 6 + [1, 2], [0.5, 0.5, 1, 0.5, 0.5, 1, 0.5, 0.5, 0.5, 0.5, 2],
            [0.25, 0.25, 0.5, 1, 0.5, 0.5, 1, 0.5, 0.5, 1, 2]],
        3: [[0.5, 0.5, 1, 1, 0.5, 0.5, 2], [1, 0.5, 0.5, 1, 3]],
        6: [[1, 1, 1, 1, 1, 1, 2, 1, 3], [2, 1, 1, 1, 1, 3, 3]]}


def melody(key, chords_by_bar, bars, bpb, rng, octave=5, density=0.5, phrase=2, leap=0.2, start_bar=0):
    """Events (bar, beat, midi, beats) for an original line over the chords.

    A two-bar motif is invented (rhythm from a vocabulary that suits the metre,
    contour from a constrained random walk), answered a step higher or lower,
    contrasted, then brought home to the tonic. Notes on strong beats are
    pulled onto a tone of the chord underneath, so the line sits in the harmony
    instead of wandering across it.
    """
    pool = SPARSE if density < 0.35 else BUSY if density > 0.75 else RHYTHMS
    vocab = pool.get(bpb, pool[4])

    def contour(nnotes):
        steps = rng.choice([-2, -1, -1, 0, 1, 1, 2] + ([3, -3, 4, -4] if rng.random() < leap else []), nnotes)
        return np.cumsum(steps) - steps[0]

    motif_r = vocab[rng.integers(len(vocab))]
    motif_c = contour(len(motif_r))
    contrast_r = vocab[rng.integers(len(vocab))]
    contrast_c = contour(len(contrast_r))
    plan = []                                   # (rhythm, contour, shift, cadence)
    sections = max(1, bars // phrase)
    for s in range(sections):
        role = s % 4
        if role == 0:
            plan.append((motif_r, motif_c, 0, False))
        elif role == 1:
            plan.append((motif_r, motif_c, int(rng.choice([-1, 1, 2])), False))
        elif role == 2:
            plan.append((contrast_r, contrast_c, int(rng.choice([1, 2, 3])), False))
        else:
            plan.append((motif_r, motif_c, 0, True))
    out = []
    base = 2 + int(rng.integers(0, 3))          # start on the 3rd-5th degree
    lo_d, hi_d = -3, 9
    for s, (rhy, con, shift, cadence) in enumerate(plan):
        bar0 = start_bar + s * phrase
        beat = 0.0
        for i, (dur, c) in enumerate(zip(rhy, con)):
            d = int(np.clip(base + shift + c, lo_d, hi_d))
            bar = bar0 + int(beat // bpb)
            bb = beat % bpb
            if bar >= start_bar + bars:
                break
            midi = key.note(d, octave)
            chord = chords_by_bar[bar % len(chords_by_bar)]
            strong = bb == 0 or (bpb == 4 and bb == 2) or (bpb == 6 and bb == 3)
            if strong or i == len(rhy) - 1:
                pcs = {c % 12 for c in chord}
                if midi % 12 not in pcs:
                    cands = [midi + o for o in range(-4, 5) if (midi + o) % 12 in pcs]
                    if cands:
                        midi = min(cands, key=lambda x: abs(x - midi))
            if cadence and i == len(rhy) - 1:
                midi = key.note(0, octave) if abs(key.note(0, octave) - midi) <= abs(key.note(7, octave) - midi) \
                    else key.note(7, octave)
            out.append((bar, bb, midi, dur))
            beat += dur
    return out


def progression(key, degrees, octave=3, size=3):
    return [key.chord(d, octave, size) for d in degrees]


# ---------------------------------------------------------------------------
# the track: buses, placement, folding, mixing
# ---------------------------------------------------------------------------

class Track:
    def __init__(self, bpm, bars, beats_per_bar=4, seed=1):
        self.bpm = bpm
        self.beat = 60.0 / bpm
        self.bars = bars
        self.bpb = beats_per_bar
        self.L = int(round(bars * beats_per_bar * self.beat * SR))
        self.tail = 10 * SR
        self.buses, self.sends, self.gains = {}, {}, {}
        self.rng = np.random.default_rng(seed)

    def bus(self, name, gain=None, reverb=None):
        if name not in self.buses:
            self.buses[name] = np.zeros((self.L + self.tail, 2))
            self.gains[name], self.sends[name] = 1.0, 0.25
        if gain is not None:
            self.gains[name] = gain
        if reverb is not None:
            self.sends[name] = reverb
        return self.buses[name]

    def at(self, bar, beat=0.0):
        return (bar * self.bpb + beat) * self.beat

    def place(self, name, seconds, audio, pan=0.5, gain=1.0, humanize=0.0):
        buf = self.bus(name)
        if humanize:
            seconds += self.rng.normal(0, humanize)
            gain *= 1 + self.rng.normal(0, humanize * 8)
        i = int(round(seconds * SR)) % self.L
        a = np.asarray(audio, dtype=float)
        if a.ndim == 1:
            a = np.stack([a * np.cos(pan * np.pi / 2), a * np.sin(pan * np.pi / 2)], 1) * 1.4142
        k = min(a.shape[0], buf.shape[0] - i)
        buf[i:i + k] += a[:k] * gain

    def bed(self, name, audio_stereo):
        self.bus(name)[: self.L] += audio_stereo[: self.L]

    def fold(self, buf):
        out = buf[: self.L].copy()
        over = buf[self.L:]
        for s in range(0, over.shape[0], self.L):
            chunk = over[s:s + self.L]
            out[: chunk.shape[0]] += chunk
        return out

    def mix(self, ir=2.6, wet=1.0, hi=None, rms_db=-21.0):
        dry = np.zeros((self.L, 2))
        send = np.zeros((self.L, 2))
        for name, buf in self.buses.items():
            b = self.fold(buf) * self.gains[name]
            dry += b
            send += b * self.sends[name]
        out = dry + circular_convolve(send, reverb_ir(ir)) * 0.35 * wet
        out = spectrum_filter(out, lo=28, hi=hi or 15000)
        out -= out.mean(axis=0)
        return master(out, rms_db)


def master(x, target_rms_db=-21.0, ceiling=0.89, knee=0.6):
    rms = np.sqrt(np.mean(x ** 2))
    x = x * (10 ** (target_rms_db / 20) / max(rms, 1e-9))
    over = np.abs(x) > knee
    x[over] = np.sign(x[over]) * (knee + (ceiling - knee) * np.tanh((np.abs(x[over]) - knee) / (ceiling - knee)))
    return x


def bars_for(bpm, bpb, seconds=48, multiple=4):
    bar_s = bpb * 60.0 / bpm
    return max(multiple, int(round(seconds / bar_s / multiple)) * multiple)


# ---------------------------------------------------------------------------
# styles. Each takes the recipe (a dict) and returns finished stereo audio.
# r["key"], r["mode"], r["bpm"], r["seed"] and r["with"] (signature sounds)
# ---------------------------------------------------------------------------

def _setup(r, default_mode, default_bpm, bpb=4, seconds=48, multiple=4):
    key = Key(r.get("key", "C"), r.get("mode", default_mode))
    bpm = r.get("bpm", default_bpm)
    tr = Track(bpm, bars_for(bpm, bpb, seconds, multiple), bpb, seed=r.get("seed", 1))
    rng = np.random.default_rng(r.get("seed", 1) * 7919 + 13)
    return key, tr, rng, set(r.get("with", []))


def style_ominous(r):
    """Dread: a low drone that never resolves, clusters a semitone apart,
    sounds placed in the dark around the listener."""
    key, tr, rng, w = _setup(r, "phrygian", 56)
    root = key.note(0, 2)
    for bar in range(0, tr.bars, 4):
        tr.place("drone", tr.at(bar), drone(root, 4 * tr.bpb * tr.beat, bright=0.12, seed=bar))
        tr.place("drone", tr.at(bar), drone(root + 7 if bar % 8 == 0 else root + 6, 4 * tr.bpb * tr.beat,
                                            bright=0.08, seed=bar + 1), gain=0.6)
    for bar in range(2, tr.bars, 4):
        top = key.note(int(rng.integers(3, 7)), 4)
        for j, nt in enumerate((top, top + 1)):          # the semitone rub
            tr.place("cluster", tr.at(bar), strings(nt, 2 * tr.bpb * tr.beat, attack=2.5, release=2.5, seed=bar * 3 + j))
    for bar in range(0, tr.bars, 8):
        tr.place("fx", tr.at(bar) - 0.05, boom(0.8, seed=bar))
        tr.place("fx", tr.at(bar + 4, 2), scrape(root + 24 + int(rng.integers(0, 6)), 4.0, seed=bar), pan=rng.uniform(0.2, 0.8))
    if "heartbeat" in w:
        for bar in range(tr.bars):
            for b in range(0, tr.bpb, 2 if tr.bpm > 70 else 1):
                tr.place("heart", tr.at(bar, b), heartbeat(0.75))
    if "musicbox" in w:   # a lullaby, wound down and out of tune
        lull = Key(r.get("key", "C"), "minor")
        ch = [lull.chord(d, 3) for d in (0, 5, 3, 4)]
        for bar, bb, nt, _ in melody(lull, ch, tr.bars // 2, tr.bpb, rng, octave=5, density=0.3, start_bar=tr.bars // 2):
            tr.place("box", tr.at(bar, bb), musicbox(nt, vel=0.6, detune_cents=rng.normal(0, 25), decay=1.4),
                     pan=0.6, humanize=0.02)
    if "calliope" in w:   # a carnival waltz that has gone wrong
        lull = Key(r.get("key", "C"), "harmonic")
        ch = [lull.chord(d, 3) for d in (0, 3, 4, 0)]
        for bar, bb, nt, d in melody(lull, ch, tr.bars, tr.bpb, rng, octave=5, density=0.55):
            tr.place("box", tr.at(bar, bb), calliope(nt, d * tr.beat * 0.9, warp=1.0, seed=bar * 9 + int(bb * 2)))
        for bar in range(tr.bars):
            c = ch[bar % 4]
            tr.place("box", tr.at(bar, 0), calliope(c[0] - 12, tr.beat * 0.8, warp=0.7, seed=bar), gain=0.8)
            for b in range(1, tr.bpb):
                for nt in c[1:]:
                    tr.place("box", tr.at(bar, b), calliope(nt, tr.beat * 0.4, warp=0.7, seed=bar * 3 + b), gain=0.35)
    if "whispers" in w:
        for _ in range(tr.bars * 2):
            tr.place("air", rng.uniform(0, tr.L / SR), whisper(rng.uniform(0.6, 1.4), vel=rng.uniform(0.4, 0.9),
                                                             seed=int(rng.integers(1e6))), pan=rng.uniform(0.05, 0.95))
    if "skitter" in w:
        for _ in range(tr.bars):
            tr.place("air", rng.uniform(0, tr.L / SR), skitter(rng.uniform(0.5, 1), seed=int(rng.integers(1e6))),
                     pan=rng.uniform(0.1, 0.9))
    if "crickets" in w:
        for _ in range(tr.bars * 3):
            tr.place("air", rng.uniform(0, tr.L / SR), cricket(rng.uniform(0.4, 1), seed=int(rng.integers(1e6))),
                     pan=rng.uniform(0.1, 0.9))
    if "banjo" in w:      # a porch string, played slow and slack
        ch = [key.chord(d, 3) for d in (0, 1, 0, 6)]
        for bar in range(0, tr.bars, 2):
            for i, nt in enumerate(ch[(bar // 2) % 4]):
                tr.place("box", tr.at(bar, i * 0.66), pluck(nt + 12, 1.2, vel=0.5, brightness=0.9, decay=0.4, bend=-0.004),
                         pan=0.35, humanize=0.01)
    if "choir" in w:
        for bar in range(0, tr.bars, 2):
            top = key.note(int(rng.integers(2, 6)), 4)
            for j, nt in enumerate((top, top + 1, top + 6)):
                tr.place("choir", tr.at(bar), choir(nt, 2 * tr.bpb * tr.beat, vowel="oo", attack=1.5, seed=bar * 5 + j), gain=0.7)
    if "shriek" in w:
        for bar in range(4, tr.bars, 8):
            top = key.note(int(rng.integers(0, 5)), 6)
            for j, nt in enumerate((top, top + 1, top + 2)):
                tr.place("cluster", tr.at(bar), strings(nt, 3 * tr.beat, attack=1.8, release=0.6, bright=0.9, seed=bar + j), gain=0.5)
            tr.place("fx", tr.at(bar + 1) - 2.0, riser(2.0, 0.7, seed=bar))
    if "toll" in w:
        for bar in range(0, tr.bars, 4):
            tr.place("fx", tr.at(bar, 1), bell(root + 12, 6.0, vel=0.6, ratio=2.76, decay=3.5), pan=0.7)
    if "wind" in w:
        tr.bed("bed", wind_bed(tr.L, r.get("seed", 1), gain=0.35))
    if "fire" in w:
        tr.bed("bed", fire_bed(tr.L, r.get("seed", 1), gain=0.6))
    if "organ" in w:
        ch = [key.chord(d, 3, 3) for d in (0, 1, 5, 0)]
        for bar in range(tr.bars):
            for j, nt in enumerate(ch[(bar // 2) % 4]):
                tr.place("organ", tr.at(bar), organ(nt, tr.bpb * tr.beat * 0.98, release=1.0, trem=0.0), gain=0.6)
    tr.bus("drone", gain=0.9, reverb=0.4)
    tr.bus("cluster", gain=0.55, reverb=0.7)
    tr.bus("fx", gain=0.8, reverb=0.6)
    tr.bus("heart", gain=0.85, reverb=0.08)
    tr.bus("box", gain=0.8, reverb=0.55)
    tr.bus("air", gain=0.7, reverb=0.6)
    tr.bus("choir", gain=0.8, reverb=0.8)
    tr.bus("bed", gain=1.0, reverb=0.1)
    tr.bus("organ", gain=0.6, reverb=0.7)
    return tr.mix(ir=4.0, wet=1.2, hi=8000, rms_db=-22.5)


CAROL_TUNE = [  # an original carol-like line in scale degrees, bars of 3
    (0, 0, 4, 1), (0, 1, 4, 1), (0, 2, 5, 1), (1, 0, 4, 2), (1, 2, 2, 1), (2, 0, 3, 1), (2, 1, 2, 1),
    (2, 2, 1, 1), (3, 0, 0, 3), (4, 0, 2, 1), (4, 1, 3, 1), (4, 2, 4, 1), (5, 0, 7, 2), (5, 2, 6, 1),
    (6, 0, 5, 1), (6, 1, 4, 1), (6, 2, 3, 1), (7, 0, 4, 3),
]


def style_festive(r):
    """Christmas: sleigh bells, glockenspiel, a carol of our own in three."""
    w = set(r.get("with", []))
    bpb = 4 if "drive" in w else 3
    key, tr, rng, w = _setup(r, "major", 120 if bpb == 4 else 132, bpb=bpb, multiple=8)
    degs = [0, 3, 4, 0, 5, 1, 4, 0]
    ch = progression(key, degs, 3)
    for bar in range(tr.bars):
        c = ch[bar % 8]
        if bpb == 3:
            tr.place("bass", tr.at(bar, 0), pluck(c[0] - 12, 1.2, vel=0.7, brightness=0.3))
            for b in (1, 2):
                for j, nt in enumerate(c):
                    tr.place("keys", tr.at(bar, b), pluck(nt + 12, 0.6, vel=0.35, brightness=0.4), pan=0.35 + 0.15 * j)
        else:
            for e in range(8):
                tr.place("bass", tr.at(bar, e * 0.5), fm_bass(c[0] - 12 + (12 if e % 2 else 0), 0.3, vel=0.7))
            for j, nt in enumerate(c):
                tr.place("keys", tr.at(bar), pad(nt + 12, tr.bpb * tr.beat, attack=0.2, release=0.5, bright=0.6, seed=bar * 3 + j), gain=0.7)
            tr.place("drums", tr.at(bar, 0), kick(0.7)); tr.place("drums", tr.at(bar, 2), kick(0.6))
            tr.place("drums", tr.at(bar, 1), clap(0.6, seed=bar)); tr.place("drums", tr.at(bar, 3), clap(0.6, seed=bar + 1))
        if "strings" in w or "epic" in w:
            for j, nt in enumerate(c):
                tr.place("strings", tr.at(bar), strings(nt + 12, tr.bpb * tr.beat * 0.98, seed=bar * 5 + j), gain=0.6)
        for b in range(tr.bpb):
            tr.place("bells", tr.at(bar, b), sleigh_bells(0.7 if b == 0 else 0.45, seed=bar * 4 + b), pan=0.62)
            if bpb == 4:
                tr.place("bells", tr.at(bar, b + 0.5), sleigh_bells(0.3, seed=bar * 4 + b + 99), pan=0.62)
    tune_inst = brass if "epic" in w else glock
    for rep in range(0, tr.bars, 8):
        for bar, bb, d, dur in CAROL_TUNE:
            if bpb == 4:
                bb = bb * 4 / 3
                dur = dur * 4 / 3
            nt = key.note(d, 5 if tune_inst is glock else 4)
            if tune_inst is glock:
                tr.place("tune", tr.at(rep + bar, bb), glock(nt, vel=0.8), pan=0.5)
            else:
                tr.place("tune", tr.at(rep + bar, bb), brass(nt, dur * tr.beat * 0.92, vel=0.8, seed=bar))
    if "epic" in w:
        for bar in range(0, tr.bars, 2):
            tr.place("drums", tr.at(bar), timpani(key.note(0, 2), 0.8))
            tr.place("drums", tr.at(bar + 1, tr.bpb - 1), timpani(key.note(4, 1), 0.6))
        for bar in range(tr.bars // 2, tr.bars):
            c = ch[bar % 8]
            tr.place("choir", tr.at(bar), choir(c[1] + 12, tr.bpb * tr.beat, vowel="ah", attack=0.6, seed=bar), gain=0.6)
    if "fire" in w:
        tr.bed("bed", fire_bed(tr.L, r.get("seed", 1), gain=0.5))
    tr.bus("bass", gain=0.7, reverb=0.1)
    tr.bus("keys", gain=0.7, reverb=0.35)
    tr.bus("strings", gain=0.6, reverb=0.5)
    tr.bus("bells", gain=0.55, reverb=0.3)
    tr.bus("tune", gain=1.0, reverb=0.4)
    tr.bus("drums", gain=0.7, reverb=0.15)
    tr.bus("choir", gain=0.7, reverb=0.6)
    tr.bus("bed", gain=1.0, reverb=0.05)
    return tr.mix(ir=2.4, hi=12000)


def style_twisted_carol(r):
    """Christmas gone wrong: the same kind of carol, in minor, on a music box
    that is losing its tune, with the sleigh bells slowed to a drag."""
    key, tr, rng, w = _setup(r, "harmonic", 84, bpb=3, multiple=8)
    root = key.note(0, 2)
    for bar in range(0, tr.bars, 4):
        tr.place("drone", tr.at(bar), drone(root, 4 * 3 * tr.beat, bright=0.1, seed=bar))
        tr.place("drone", tr.at(bar), drone(root + 6, 4 * 3 * tr.beat, bright=0.05, seed=bar + 3), gain=0.5)
    for rep in range(0, tr.bars, 8):
        for bar, bb, d, dur in CAROL_TUNE:
            tr.place("box", tr.at(rep + bar, bb), musicbox(key.note(d, 5), vel=0.7,
                                                           detune_cents=rng.normal(-15 * (rep > 0), 20), decay=1.3),
                     pan=0.55, humanize=0.015)
    for bar in range(tr.bars):
        tr.place("bells", tr.at(bar, 0), sleigh_bells(0.5, seed=bar, slow=2.2), pan=0.3)
    for bar in range(0, tr.bars, 8):
        tr.place("fx", tr.at(bar) - 0.05, boom(0.7, seed=bar))
    if "stabs" in w:
        for bar in range(3, tr.bars, 4):
            top = key.note(4, 4)
            for j, nt in enumerate((top, top + 1, top + 6)):
                tr.place("fx", tr.at(bar, 2), strings(nt, 0.4, attack=0.01, release=0.3, bright=0.95, seed=bar + j), gain=0.9)
        for bar in range(7, tr.bars, 8):
            tr.place("fx", tr.at(bar + 1) - 2.0, riser(2.0, 0.6, seed=bar))
    if "wind" in w:
        tr.bed("bed", wind_bed(tr.L, r.get("seed", 1), gain=0.35))
    tr.bus("drone", gain=0.8, reverb=0.4)
    tr.bus("box", gain=1.0, reverb=0.6)
    tr.bus("bells", gain=0.5, reverb=0.7)
    tr.bus("fx", gain=0.7, reverb=0.6)
    tr.bus("bed", gain=1.0, reverb=0.1)
    return tr.mix(ir=3.8, wet=1.2, hi=9000, rms_db=-22.5)


def style_halloween(r):
    """Spooky but fun: a minor waltz with pizzicato, celesta and a theremin."""
    w = set(r.get("with", []))
    if "mariachi" in w:
        return style_mariachi(r)
    key, tr, rng, w = _setup(r, "harmonic", 138, bpb=3, multiple=8)
    degs = [0, 3, 4, 0, 5, 3, 4, 4]
    ch = progression(key, degs, 3)
    for bar in range(tr.bars):
        c = ch[bar % 8]
        tr.place("bass", tr.at(bar, 0), pluck(c[0] - 12, 0.8, vel=0.8, brightness=0.3, decay=0.5))
        for b in (1, 2):
            for nt in c:
                tr.place("pizz", tr.at(bar, b), pluck(nt + 12, 0.4, vel=0.35, brightness=0.35, decay=0.4))
    lead = melody(key, ch, tr.bars, 3, rng, octave=5, density=0.55)
    if "theremin" in w:
        half = [(b * 3 + bb, nt, d) for b, bb, nt, d in lead if b >= tr.bars // 2]
        if half:
            s0 = half[0][0]
            line = theremin_line([(b - s0, nt, d) for b, nt, d in half], tr.beat, vel=0.8)
            tr.place("lead", s0 * tr.beat, line, pan=0.55)
        lead = [e for e in lead if e[0] < tr.bars // 2]
    inst = glock if "sparkle" in w else marimba
    for bar, bb, nt, d in lead:
        tr.place("lead", tr.at(bar, bb), inst(nt, vel=0.75), pan=0.5)
    if "sparkle" in w:
        for _ in range(tr.bars):
            run_start = rng.uniform(0, tr.L / SR)
            for k in range(5):
                tr.place("fx", run_start + k * 0.06, glock(key.note(7 + k * 2, 5), vel=0.3), pan=0.7)
    if "wind" in w:
        tr.bed("bed", wind_bed(tr.L, r.get("seed", 1), gain=0.25))
    for bar in range(0, tr.bars, 2):
        tr.place("perc", tr.at(bar, 0), woodblock(0.5, pitch=0.8))
        tr.place("perc", tr.at(bar, 2), woodblock(0.35, pitch=1.1))
    tr.bus("bass", gain=0.8, reverb=0.15)
    tr.bus("pizz", gain=0.6, reverb=0.3)
    tr.bus("lead", gain=0.9, reverb=0.4)
    tr.bus("fx", gain=0.6, reverb=0.6)
    tr.bus("perc", gain=0.6, reverb=0.3)
    tr.bus("bed", gain=1.0, reverb=0.1)
    return tr.mix(ir=2.4, hi=11000)


def style_mariachi(r):
    """Day of the Dead: nylon-string strums in three, a bright horn line."""
    key, tr, rng, w = _setup(r, "harmonic", 150, bpb=3, multiple=8)
    degs = [0, 0, 4, 4, 4, 4, 0, 0, 3, 3, 0, 0, 4, 4, 0, 0]
    ch = progression(key, degs, 3)
    for bar in range(tr.bars):
        c = ch[bar % len(ch)]
        tr.place("bass", tr.at(bar, 0), pluck(c[0] - 12, 0.9, vel=0.85, brightness=0.25, decay=0.6))
        tr.place("gtr", tr.at(bar, 1), strum([n + 12 for n in c], 0.5, vel=0.55, brightness=0.5), pan=0.35)
        tr.place("gtr", tr.at(bar, 2), strum([n + 12 for n in c], 0.5, vel=0.5, brightness=0.5, down=False), pan=0.35)
    for bar, bb, nt, d in melody(key, ch, tr.bars, 3, rng, octave=5, density=0.6):
        tr.place("horn", tr.at(bar, bb), brass(nt, d * tr.beat * 0.85, vel=0.75, seed=bar), pan=0.6)
        tr.place("horn", tr.at(bar, bb), brass(nt - 4 if (nt - 4) % 12 in {c % 12 for c in ch[bar % len(ch)]} else nt - 3,
                                               d * tr.beat * 0.85, vel=0.45, seed=bar + 5), pan=0.68)
    for bar in range(tr.bars):
        tr.place("perc", tr.at(bar, 0), shaker(0.5, seed=bar)); tr.place("perc", tr.at(bar, 1.5), shaker(0.4, seed=bar + 3))
    tr.bus("bass", gain=0.8, reverb=0.1)
    tr.bus("gtr", gain=0.8, reverb=0.3)
    tr.bus("horn", gain=0.8, reverb=0.35)
    tr.bus("perc", gain=0.6, reverb=0.2)
    return tr.mix(ir=2.0, hi=12000)


def style_synthwave(r):
    """Neon at night: driving octave bass, sixteenth arpeggio, gated drums."""
    key, tr, rng, w = _setup(r, "minor", 100, multiple=8)
    degs = r.get("chords", [0, 5, 2, 6])
    ch = progression(key, degs, 3)
    dark = "dark" in w
    chill = "chill" in w
    for bar in range(tr.bars):
        c = ch[bar % len(ch)]
        for j, nt in enumerate(c):
            tr.place("pad", tr.at(bar), pad(nt + 12, tr.bpb * tr.beat, attack=0.3, release=0.8,
                                            bright=0.3 if chill else 0.55, seed=bar * 3 + j))
        for e in range(8 if not chill else 4):
            step = 0.5 if not chill else 1
            tr.place("bass", tr.at(bar, e * step), bass(c[0] - 12 + (12 if (e % 2 and not dark) else 0), step * tr.beat * 0.8,
                                                        vel=0.9 if e % 2 == 0 else 0.7))
        if not chill:
            arp = [c[0] + 24, c[1] + 24, c[2] + 24, c[1] + 24] if not dark else [c[0] + 12, c[0] + 24, c[1] + 12, c[2] + 12]
            for s in range(16):
                tr.place("arp", tr.at(bar, s * 0.25), saw_lead(arp[s % 4], 0.16, vel=0.5 + 0.15 * (s % 4 == 0),
                                                             release=0.06, bright=0.45), pan=0.35 if s % 2 else 0.65)
        if bar >= 2:
            for b in range(4):
                if chill and b % 2:
                    continue
                tr.place("drums", tr.at(bar, b), kick(0.75, tight=True))
            for b in (1, 3):
                tr.place("drums", tr.at(bar, b), snare(0.55, seed=bar * 2 + b) if not chill else rim(0.6, seed=bar + b))
            for b in range(8):
                tr.place("drums", tr.at(bar, b * 0.5 + (0.06 if chill and b % 2 else 0)), hat(0.35 + 0.15 * (b % 2), seed=bar * 8 + b), pan=0.6)
    lead = melody(key, ch, tr.bars // 2, 4, rng, octave=5 if not dark else 4, density=0.45 if not chill else 0.3,
                  start_bar=tr.bars // 2)
    for bar, bb, nt, d in lead:
        tr.place("lead", tr.at(bar, bb), saw_lead(nt, d * tr.beat * 0.92, vel=0.75, release=0.35, bright=0.8) if not chill
                 else epiano(nt, d * tr.beat, vel=0.7), pan=0.5)
    if "rain" in w:
        tr.bed("bed", rain_bed(tr.L, r.get("seed", 1), gain=0.22))
    if "growl" in w:
        for bar in range(0, tr.bars, 4):
            tr.place("fx", tr.at(bar), scrape(key.note(0, 2), 3.0, vel=0.7, seed=bar))
    tr.bus("pad", gain=0.55, reverb=0.45)
    tr.bus("bass", gain=0.75, reverb=0.03)
    tr.bus("arp", gain=0.55, reverb=0.35)
    tr.bus("drums", gain=0.7, reverb=0.15)
    tr.bus("lead", gain=0.8, reverb=0.45)
    tr.bus("bed", gain=1.0, reverb=0.05)
    tr.bus("fx", gain=0.5, reverb=0.6)
    return tr.mix(ir=2.2, hi=12000)


def style_cyber(r):
    """Machines thinking: tight drums, FM bass stabs, data blips, glitches."""
    key, tr, rng, w = _setup(r, "minor", 116, multiple=8)
    degs = r.get("chords", [0, 0, 5, 6])
    ch = progression(key, degs, 3)
    bass_pat = rng.choice([[1, 0, 0, 1, 0, 0, 1, 0, 1, 0, 0, 1, 0, 1, 0, 0],
                           [1, 0, 1, 0, 0, 1, 0, 0, 1, 0, 1, 0, 0, 1, 0, 1],
                           [1, 0, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 1, 0, 0, 0]])
    for bar in range(tr.bars):
        c = ch[bar % len(ch)]
        for s in range(16):
            if bass_pat[s]:
                tr.place("bass", tr.at(bar, s * 0.25), fm_bass(c[0] - 12, 0.2, vel=0.8, ratio=0.5 if "dark" in w else 1.0,
                                                             bite=3.5))
        for j, nt in enumerate(c):
            tr.place("pad", tr.at(bar), pad(nt + 12, tr.bpb * tr.beat, attack=0.6, release=1.0, bright=0.25, seed=bar * 3 + j))
        if bar >= 1:
            for b in range(4):
                tr.place("drums", tr.at(bar, b), kick(0.8, tight=True))
            for b in (1, 3):
                tr.place("drums", tr.at(bar, b), clap(0.6, seed=bar * 2 + b))
            for s in range(16):
                if s % 2 or rng.random() < 0.3:
                    tr.place("drums", tr.at(bar, s * 0.25), hat(0.25 + 0.2 * (s % 4 == 2), seed=bar * 16 + s), pan=0.62)
        seq = [c[int(rng.integers(0, 3))] + 24 + 12 * int(rng.integers(0, 2)) for _ in range(8)]
        for s in range(8):
            if rng.random() < 0.8:
                tone = crush(saw_lead(seq[s], 0.1, vel=0.6, release=0.03, bright=0.4), bits=5, hold=3)
                tr.place("seq", tr.at(bar, s * 0.5 + 0.25), tone, pan=0.3 + 0.4 * (s % 2))
    scale = [key.note(d, 6) for d in range(0, 7)]
    n_blips = tr.bars * (6 if "chatter" in w else 3)
    for _ in range(n_blips):
        bar, s = int(rng.integers(0, tr.bars)), int(rng.integers(0, 16))
        tr.place("data", tr.at(bar, s * 0.25), beep(int(rng.choice(scale)), length=rng.choice([0.03, 0.05, 0.08]),
                                                   vel=0.6, square=rng.random() < 0.4), pan=rng.uniform(0.15, 0.85))
    if "glitch" in w:
        for bar in range(3, tr.bars, 4):
            src = fm_bass(ch[bar % len(ch)][0] + 12, 0.08, vel=0.7, bite=5)
            for k in range(6):
                tr.place("data", tr.at(bar, 3.5) + k * 0.045, src * (1 - k / 7), pan=0.5)
    if "typing" in w:
        for _ in range(tr.bars * 5):
            tr.place("data", rng.uniform(0, tr.L / SR), woodblock(0.25, pitch=rng.uniform(1.8, 2.6)), pan=rng.uniform(0.3, 0.7))
    if "rain" in w:
        tr.bed("bed", rain_bed(tr.L, r.get("seed", 1), gain=0.25))
    if "hum" in w:
        tr.bed("bed", hum_bed(tr.L, r.get("seed", 1), gain=0.12))
    lead = melody(key, ch, tr.bars // 2, 4, rng, octave=5, density=0.5, start_bar=tr.bars // 2)
    for bar, bb, nt, d in lead:
        tr.place("lead", tr.at(bar, bb), chip(nt, d * tr.beat * 0.85, vel=0.7, duty=0.25), pan=0.5)
    tr.bus("bass", gain=0.75, reverb=0.03)
    tr.bus("pad", gain=0.45, reverb=0.5)
    tr.bus("drums", gain=0.7, reverb=0.1)
    tr.bus("seq", gain=0.6, reverb=0.3)
    tr.bus("data", gain=0.6, reverb=0.4)
    tr.bus("lead", gain=0.75, reverb=0.35)
    tr.bus("bed", gain=1.0, reverb=0.05)
    return tr.mix(ir=1.8, hi=13000)


def style_lab(r):
    """Clean future: soft sine arpeggios, glassy plucks, nothing out of place."""
    key, tr, rng, w = _setup(r, "lydian", 80, multiple=8)
    ch = progression(key, [0, 1, 4, 5] if key.mode == "lydian" else [0, 5, 3, 4], 3, size=4)
    for bar in range(tr.bars):
        c = ch[bar % len(ch)]
        for j, nt in enumerate(c):
            tr.place("pad", tr.at(bar), pad(nt + 12, tr.bpb * tr.beat, attack=1.0, release=1.5, bright=0.2, seed=bar * 4 + j))
        arp = [c[0] + 24, c[2] + 24, c[1] + 24, c[3] + 24, c[2] + 24, c[1] + 36, c[3] + 24, c[2] + 24]
        for s, nt in enumerate(arp):
            tr.place("arp", tr.at(bar, s * 0.5), bell(nt, 1.8, vel=0.45, ratio=2.0, decay=0.7), pan=0.2 + 0.08 * s)
        tr.place("pulse", tr.at(bar, 0), kick(0.35, tight=True))
        tr.place("pulse", tr.at(bar, 2.5), kick(0.25, tight=True))
    for bar, bb, nt, d in melody(key, ch, tr.bars // 2, 4, rng, octave=5, density=0.3, start_bar=tr.bars // 2):
        tr.place("lead", tr.at(bar, bb), pluck(nt, 2.0, vel=0.6, brightness=0.8, decay=1.5), pan=0.55)
    tr.bed("bed", hum_bed(tr.L, r.get("seed", 1), gain=0.08, base=60.0))
    tr.bus("pad", gain=0.6, reverb=0.6)
    tr.bus("arp", gain=0.55, reverb=0.55)
    tr.bus("pulse", gain=0.6, reverb=0.2)
    tr.bus("lead", gain=0.8, reverb=0.6)
    tr.bus("bed", gain=1.0, reverb=0.1)
    return tr.mix(ir=3.0, hi=12000, rms_db=-22.0)


def style_chiptune(r):
    """Game-console music: two pulse channels, a triangle, a noise drum."""
    key, tr, rng, w = _setup(r, "major", 132, multiple=8)
    degs = r.get("chords", [0, 5, 3, 4])
    ch = progression(key, degs, 3)
    for bar in range(tr.bars):
        c = ch[bar % len(ch)]
        for e in range(8):
            tr.place("tri", tr.at(bar, e * 0.5), chip_tri(c[0] - 12 + (7 if e % 4 == 2 else 0) + (12 if e % 2 else 0),
                                                        0.42 * tr.beat))
        for s in range(16):
            tr.place("arp", tr.at(bar, s * 0.25), chip(c[s % 3] + 24, 0.2 * tr.beat, vel=0.45, duty=0.125), pan=0.4)
        for b in range(4):
            tr.place("noise", tr.at(bar, b), chip_noise(0.8 if b % 2 == 0 else 0.5, length=0.09 if b % 2 else 0.05,
                                                       seed=bar * 4 + b, tone=24 if b % 2 == 0 else 3))
            tr.place("noise", tr.at(bar, b + 0.5), chip_noise(0.3, length=0.03, seed=bar * 4 + b + 50, tone=2))
    for bar, bb, nt, d in melody(key, ch, tr.bars, 4, rng, octave=5, density=0.6):
        tr.place("lead", tr.at(bar, bb), chip(nt, d * tr.beat * 0.9, vel=0.8, duty=0.5 if "melancholy" in w else 0.25,
                                              slide=-0.03 if bb == 0 else 0), pan=0.6)
    tr.bus("tri", gain=0.8, reverb=0.02)
    tr.bus("arp", gain=0.5, reverb=0.1)
    tr.bus("noise", gain=0.6, reverb=0.05)
    tr.bus("lead", gain=0.85, reverb=0.15)
    return tr.mix(ir=1.2, wet=0.6, hi=12000)


def style_space(r):
    """Out there: wide slow pads, a sub drone, bells far away, starlight."""
    key, tr, rng, w = _setup(r, "minor", 56, seconds=52)
    degs = r.get("chords", [0, 5, 2, 6])
    ch = progression(key, degs, 3, size=4)
    for bar in range(tr.bars):
        c = ch[bar % len(ch)]
        for j, nt in enumerate(c):
            tr.place("pad", tr.at(bar), pad(nt + 12, tr.bpb * tr.beat, attack=2.0, release=3.0, bright=0.35, seed=bar * 5 + j))
        tr.place("drone", tr.at(bar), drone(c[0] - 12, tr.bpb * tr.beat, seed=bar))
    for _ in range(int(tr.bars * 1.2)):
        bar, b = int(rng.integers(0, tr.bars)), int(rng.integers(0, 4))
        c = ch[bar % len(ch)]
        nt = int(rng.choice(c)) + 36
        tr.place("bells", tr.at(bar, b), bell(nt, 5.0, vel=0.4, ratio=3.5, decay=3.0) if "twinkle" not in w
                 else musicbox(nt, vel=0.45, decay=1.6), pan=rng.uniform(0.15, 0.85))
    if "awe" in w:
        for bar in range(tr.bars // 2, tr.bars):
            c = ch[bar % len(ch)]
            tr.place("choir", tr.at(bar), choir(c[2] + 12, tr.bpb * tr.beat, vowel="ah", seed=bar), gain=0.8)
    if "ominous" in w:
        for bar in range(0, tr.bars, 4):
            tr.place("fx", tr.at(bar), boom(0.6, seed=bar))
            tr.place("fx", tr.at(bar + 2), scrape(key.note(0, 3), 4.0, vel=0.5, seed=bar))
    if "storm" in w:
        for bar in range(1, tr.bars, 3):
            tr.place("fx", tr.at(bar, rng.uniform(0, 3)), boom(0.5, seed=bar + 7), pan=rng.uniform(0.2, 0.8))
        tr.bed("bed", wind_bed(tr.L, r.get("seed", 1), gain=0.2))
    if "pulse" in w:
        for bar in range(tr.bars):
            for b in range(4):
                tr.place("fx", tr.at(bar, b), blip(key.note(0, 4), vel=0.35, length=0.3, rise=0.0))
    shimmer = loop_noise(tr.L, 9, lo=5000, hi=9000, gain=0.015) * (0.5 + 0.5 * lfo(tr.L, 2))[:, None]
    tr.bed("bed", shimmer)
    tr.bus("pad", gain=0.85, reverb=0.6)
    tr.bus("drone", gain=0.6, reverb=0.3)
    tr.bus("bells", gain=0.7, reverb=0.9)
    tr.bus("choir", gain=0.7, reverb=0.8)
    tr.bus("fx", gain=0.6, reverb=0.7)
    tr.bus("bed", gain=1.0, reverb=0.4)
    return tr.mix(ir=4.2, wet=1.3, hi=11000, rms_db=-22.0)


def style_underwater(r):
    """Below the surface: a swelling drone, slow plucks, rising bubbles."""
    key, tr, rng, w = _setup(r, "dorian", 60, seconds=50)
    degs = r.get("chords", [0, 0, 5, 6])
    ch = progression(key, degs, 2, size=4)
    bright = "bright" in w
    for bar in range(tr.bars):
        c = ch[bar % len(ch)]
        for j, nt in enumerate(c):
            tr.place("pad", tr.at(bar), pad(nt + (24 if bright else 12), tr.bpb * tr.beat, attack=1.8, release=2.5,
                                            bright=0.35 if bright else 0.12, detune=10, seed=bar * 3 + j))
        for k in range(3 if not bright else 6):
            nt = c[k % len(c)] + (24 if not bright else 36)
            step = 1.33 if not bright else 0.66
            tr.place("pluck", tr.at(bar, k * step), (pluck(nt, 2.5, vel=0.55, brightness=0.25) if not bright
                                                     else marimba(nt, vel=0.5)), pan=0.3 + 0.1 * k, humanize=0.006)
    for _ in range(tr.bars * (4 if bright else 2)):
        tr.place("bubbles", rng.uniform(0, tr.L / SR), blip(int(rng.integers(76, 94)), vel=rng.uniform(0.3, 0.7)),
                 pan=rng.uniform(0.15, 0.85))
    if "eerie" in w:
        for bar in range(2, tr.bars, 4):
            tr.place("fx", tr.at(bar), scrape(key.note(4, 4), 5.0, vel=0.45, seed=bar), pan=rng.uniform(0.2, 0.8))
    if "lure" in w:
        for bar in range(0, tr.bars, 2):
            tr.place("fx", tr.at(bar, 1), bell(key.note(4, 6), 4.0, vel=0.4, ratio=1.41, decay=2.0), pan=0.6)
    swell = loop_noise(tr.L, 12, lo=60, hi=400, gain=0.35) * (0.6 + 0.4 * lfo(tr.L, 3))[:, None]
    tr.bed("deep", swell * (0.5 if bright else 1.0))
    tr.bus("pad", gain=0.9, reverb=0.55)
    tr.bus("pluck", gain=0.8, reverb=0.6)
    tr.bus("bubbles", gain=0.6, reverb=0.7)
    tr.bus("fx", gain=0.6, reverb=0.8)
    tr.bus("deep", gain=0.5, reverb=0.4)
    return tr.mix(ir=3.5, wet=1.2, hi=7000 if not bright else 11000, rms_db=-22.0)


def style_epic(r):
    """Battle and legend: low strings, brass, timpani and war drums."""
    w = set(r.get("with", []))
    bpb = 6 if "gallop" in w else 4
    key, tr, rng, w = _setup(r, "minor", 92 if bpb == 4 else 150, bpb=bpb, multiple=8)
    degs = r.get("chords", [0, 5, 2, 6])
    ch = progression(key, degs, 3)
    for bar in range(tr.bars):
        c = ch[bar % len(ch)]
        for j, nt in enumerate(c):
            tr.place("strings", tr.at(bar), strings(nt, tr.bpb * tr.beat * 0.98, attack=0.3, bright=0.5, seed=bar * 3 + j))
        tr.place("strings", tr.at(bar), strings(c[0] - 12, tr.bpb * tr.beat * 0.98, attack=0.2, bright=0.4, seed=bar + 77))
        if bpb == 4:
            hits = [0, 1.5, 2, 3] if "march" not in w else [0, 2]
            for b in hits:
                tr.place("drums", tr.at(bar, b), taiko(0.85 if b == 0 else 0.6, seed=bar * 4 + int(b * 2)), pan=0.45)
            if "march" in w:
                for s in range(8):
                    tr.place("drums", tr.at(bar, s * 0.5), snare(0.5 + 0.3 * (s % 4 == 0), seed=bar * 8 + s, march=True), pan=0.55)
        else:
            for b in (0, 2, 3, 5):        # the gallop: da-da-DUM
                tr.place("drums", tr.at(bar, b), frame_drum(0.8 if b in (0, 3) else 0.5, seed=bar * 6 + b))
            for e in range(6):
                tr.place("ost", tr.at(bar, e), strings(c[0] + (12 if e % 3 == 0 else 7), tr.beat * 0.5, attack=0.01,
                                                      release=0.15, bright=0.6, seed=bar * 6 + e), gain=0.6)
        if bar % 2 == 0:
            tr.place("drums", tr.at(bar), timpani(key.note(0, 2), 0.8))
    if "nordic" in w:
        for bar in range(0, tr.bars, 2):
            tr.place("strings", tr.at(bar), drone(key.note(0, 2), 2 * tr.bpb * tr.beat, bright=0.3, seed=bar), gain=0.7)
            tr.place("strings", tr.at(bar), drone(key.note(4, 2), 2 * tr.bpb * tr.beat, bright=0.3, seed=bar + 1), gain=0.5)
    for bar, bb, nt, d in melody(key, ch, tr.bars // 2, bpb, rng, octave=4, density=0.35, start_bar=tr.bars // 2):
        tr.place("brass", tr.at(bar, bb), brass(nt, d * tr.beat * 0.92, vel=0.85, seed=bar), pan=0.5)
        tr.place("brass", tr.at(bar, bb), brass(nt - 12, d * tr.beat * 0.92, vel=0.6, seed=bar + 3), pan=0.4)
    if "choir" in w:
        for bar in range(tr.bars // 2, tr.bars):
            c = ch[bar % len(ch)]
            for j, nt in enumerate(c):
                tr.place("choir", tr.at(bar), choir(nt + 12, tr.bpb * tr.beat, vowel="ah", attack=0.4, seed=bar * 3 + j), gain=0.5)
    if "fire" in w:
        tr.bed("bed", fire_bed(tr.L, r.get("seed", 1), gain=0.4))
    if "organ" in w:
        for bar in range(tr.bars):
            for nt in ch[bar % len(ch)]:
                tr.place("choir", tr.at(bar), organ(nt, tr.bpb * tr.beat * 0.98, release=0.8, trem=0.0), gain=0.6)
    tr.bus("strings", gain=0.75, reverb=0.45)
    tr.bus("ost", gain=0.6, reverb=0.3)
    tr.bus("drums", gain=0.85, reverb=0.35)
    tr.bus("brass", gain=0.85, reverb=0.45)
    tr.bus("choir", gain=0.7, reverb=0.7)
    tr.bus("bed", gain=1.0, reverb=0.05)
    return tr.mix(ir=3.2, hi=11000)


def style_eastern(r):
    """Japan and China: pentatonic lines on koto and shakuhachi, taiko."""
    key, tr, rng, w = _setup(r, "hirajoshi", 72, seconds=50)
    festive = "festive" in w
    if festive:
        key = Key(r.get("key", "D"), r.get("mode", "pent_major"))
    harm = Key(r.get("key", "D"), "minor" if not festive else "major")
    ch = progression(harm, [0, 5, 3, 4] if festive else [0, 0, 5, 4], 3)
    for bar in range(tr.bars):
        c = ch[bar % len(ch)]
        tr.place("drone", tr.at(bar), drone(harm.note(0, 2), tr.bpb * tr.beat, bright=0.15, seed=bar), gain=0.6)
        pat = [0, 2, 4, 2, 3, 4, 5, 4] if not festive else [0, 1, 2, 3, 4, 3, 2, 1]
        step = 0.5
        for s, p in enumerate(pat):
            if not festive and rng.random() < 0.35:
                continue
            nt = key.note(p, 4)
            tr.place("koto", tr.at(bar, s * step), pluck(nt, 1.8, vel=0.55, brightness=0.85, decay=0.8, bend=0.006),
                     pan=0.3 + 0.05 * s, humanize=0.01)
        if festive:
            for b in range(4):
                tr.place("perc", tr.at(bar, b), woodblock(0.5 if b % 2 == 0 else 0.35, pitch=1.0 + 0.2 * (b % 2)))
            tr.place("perc", tr.at(bar, 0), taiko(0.6, pitch=1.2, seed=bar))
        else:
            if bar % 2 == 0:
                tr.place("perc", tr.at(bar, 0), taiko(0.75, seed=bar))
            if "tense" in w and bar % 4 == 3:
                for k, b in enumerate((2, 2.5, 3, 3.25, 3.5)):
                    tr.place("perc", tr.at(bar, b), taiko(0.4 + 0.1 * k, pitch=1.3, seed=bar * 9 + k))
    lead = melody(key, [[key.note(0, 3), key.note(2, 3), key.note(4, 3)]], tr.bars, 4, rng, octave=5,
                  density=0.3 if not festive else 0.55)
    for bar, bb, nt, d in lead:
        if festive:
            tr.place("lead", tr.at(bar, bb), flute(nt + 12, d * tr.beat * 0.9, vel=0.7, seed=bar), pan=0.55)
        else:
            tr.place("lead", tr.at(bar, bb), flute(nt, d * tr.beat * 0.95, vel=0.8, breath=0.3, scoop=0.03, seed=bar * 4 + int(bb)),
                     pan=0.55)
    if "rain" in w:
        tr.bed("bed", rain_bed(tr.L, r.get("seed", 1), gain=0.3))
    if "bells" in w:
        for bar in range(0, tr.bars, 2):
            tr.place("lead", tr.at(bar, 3), bell(key.note(4, 6), 3.0, vel=0.35, ratio=2.0, decay=1.5), pan=0.7)
    if "ominous" in w:
        for bar in range(0, tr.bars, 4):
            tr.place("perc", tr.at(bar), boom(0.6, seed=bar))
    if "wind" in w:
        tr.bed("bed", wind_bed(tr.L, r.get("seed", 1), gain=0.25))
    tr.bus("drone", gain=0.6, reverb=0.4)
    tr.bus("koto", gain=0.85, reverb=0.4)
    tr.bus("perc", gain=0.7, reverb=0.4)
    tr.bus("lead", gain=0.85, reverb=0.55)
    tr.bus("bed", gain=1.0, reverb=0.05)
    return tr.mix(ir=3.0, hi=11000)


def style_forest(r):
    """Enchanted woods: harp figures, a flute, celesta glimmers."""
    w = set(r.get("with", []))
    if "jig" in w:
        return style_jig(r)
    key, tr, rng, w = _setup(r, "major", 84, multiple=8)
    degs = r.get("chords", [0, 5, 3, 4])
    ch = progression(key, degs, 3, size=4)
    for bar in range(tr.bars):
        c = ch[bar % len(ch)]
        fig = [c[0] + 12, c[1] + 12, c[2] + 12, c[3] + 12, c[2] + 24, c[3] + 12, c[2] + 12, c[1] + 12]
        for e, nt in enumerate(fig):
            tr.place("harp", tr.at(bar, e * 0.5), pluck(nt, 2.0, vel=0.6 if e else 0.75, brightness=0.55),
                     pan=0.3 + 0.05 * e, humanize=0.005)
        tr.place("bass", tr.at(bar), bass(c[0], 3.2, vel=0.5))
        if "playful" in w:
            for b in (1, 3):
                tr.place("perc", tr.at(bar, b), pluck(c[0] + 12, 0.3, vel=0.5, brightness=0.3, decay=0.3), pan=0.6)
        if bar >= 4 and "shaker" in w:
            for b in range(8):
                tr.place("perc", tr.at(bar, b * 0.5), shaker(0.6 if b % 2 else 0.35, seed=bar * 8 + b), pan=0.7)
    for bar, bb, nt, d in melody(key, ch, tr.bars // 2, 4, rng, octave=5, density=0.4, start_bar=tr.bars // 2):
        tr.place("lead", tr.at(bar, bb), flute(nt, d * tr.beat * 0.92, vel=0.7, seed=bar * 4 + int(bb)), pan=0.55)
    if "magic" in w:
        for _ in range(tr.bars):
            t0 = rng.uniform(0, tr.L / SR)
            for k in range(4):
                tr.place("fx", t0 + k * 0.07, glock(key.note(4 + 2 * k, 6), vel=0.25), pan=0.75)
    if "birds" in w:
        for i in range(tr.bars // 2):
            tr.place("fx", rng.uniform(0, tr.L / SR), chirp(int(rng.integers(98, 106)), notes=int(rng.integers(2, 4)), seed=i),
                     pan=rng.uniform(0.1, 0.9))
    if "stream" in w:
        tr.bed("bed", stream_bed(tr.L, r.get("seed", 1), gain=0.18))
    tr.bus("harp", gain=0.85, reverb=0.35)
    tr.bus("bass", gain=0.55, reverb=0.05)
    tr.bus("perc", gain=0.5, reverb=0.2)
    tr.bus("lead", gain=0.9, reverb=0.45)
    tr.bus("fx", gain=0.6, reverb=0.7)
    tr.bus("bed", gain=1.0, reverb=0.05)
    return tr.mix(ir=2.2, hi=12000)


def style_jig(r):
    """A Celtic jig: tin whistle, bodhran, a strummed guitar in 6/8."""
    key, tr, rng, w = _setup(r, "mixolydian", 330, bpb=6, multiple=8)
    ch = progression(key, [0, 0, 6, 6, 0, 0, 4, 4], 3)
    for bar in range(tr.bars):
        c = ch[bar % len(ch)]
        tr.place("gtr", tr.at(bar, 0), strum([n + 12 for n in c], 0.6, vel=0.6), pan=0.35)
        tr.place("gtr", tr.at(bar, 3), strum([n + 12 for n in c], 0.6, vel=0.5, down=False), pan=0.35)
        tr.place("bass", tr.at(bar, 0), pluck(c[0] - 12, 1.0, vel=0.7, brightness=0.3))
        for b, v in ((0, 0.8), (2, 0.4), (3, 0.65), (5, 0.4)):
            tr.place("perc", tr.at(bar, b), frame_drum(v, seed=bar * 6 + b))
    for bar, bb, nt, d in melody(key, ch, tr.bars, 6, rng, octave=5, density=0.8):
        tr.place("lead", tr.at(bar, bb), flute(nt + 12, d * tr.beat * 0.85, vel=0.75, breath=0.08, seed=bar * 6 + int(bb)), pan=0.58)
    tr.bus("gtr", gain=0.75, reverb=0.25)
    tr.bus("bass", gain=0.6, reverb=0.05)
    tr.bus("perc", gain=0.75, reverb=0.2)
    tr.bus("lead", gain=0.9, reverb=0.3)
    return tr.mix(ir=1.8, hi=12000)


def style_quirky(r):
    """Cartoon mischief: staccato bassoon, pizzicato, woodblocks, a whistle."""
    key, tr, rng, w = _setup(r, "major", 120, multiple=8)
    degs = r.get("chords", [0, 3, 4, 0])
    ch = progression(key, degs, 3)
    frantic = "frantic" in w
    for bar in range(tr.bars):
        c = ch[bar % len(ch)]
        for b in range(4):
            nt = c[0] - 12 if b % 2 == 0 else c[2] - 12
            tr.place("bass", tr.at(bar, b), reed(nt, tr.beat, vel=0.9, staccato=True), pan=0.45)
        for b in (1, 3):
            for nt in c:
                tr.place("pizz", tr.at(bar, b), pluck(nt + 12, 0.3, vel=0.4, brightness=0.4, decay=0.3), pan=0.6)
        tr.place("perc", tr.at(bar, 0), woodblock(0.6)); tr.place("perc", tr.at(bar, 2), woodblock(0.5, pitch=1.3))
        if frantic:
            for b in range(8):
                tr.place("perc", tr.at(bar, b * 0.5), shaker(0.5, seed=bar * 8 + b), pan=0.7)
    lead = melody(key, ch, tr.bars, 4, rng, octave=5, density=0.75 if frantic else 0.6, leap=0.5)
    inst = r.get("lead", "marimba")
    for bar, bb, nt, d in lead:
        if inst == "whistle":
            tr.place("lead", tr.at(bar, bb), flute(nt + 12, d * tr.beat * 0.6, vel=0.6, breath=0.05), pan=0.55)
        elif inst == "ukulele":
            tr.place("lead", tr.at(bar, bb), pluck(nt, 0.8, vel=0.7, brightness=0.75, decay=0.5), pan=0.55)
        elif inst == "bassoon":
            tr.place("lead", tr.at(bar, bb), reed(nt - 12, d * tr.beat, vel=0.8, staccato=True), pan=0.55)
        else:
            tr.place("lead", tr.at(bar, bb), marimba(nt, vel=0.8, bright=0.6), pan=0.55)
    if "clank" in w:
        for _ in range(tr.bars):
            bar, b = int(rng.integers(0, tr.bars)), int(rng.integers(0, 8))
            tr.place("perc", tr.at(bar, b * 0.5), clank(0.7, seed=bar * 8 + b), pan=rng.uniform(0.2, 0.8))
    if "squeak" in w:
        for i in range(tr.bars // 2):
            tr.place("fx", rng.uniform(0, tr.L / SR), blip(int(rng.integers(88, 96)), vel=0.6, length=0.07, rise=0.6), pan=rng.uniform(0.2, 0.8))
    if "fire" in w:
        tr.bed("bed", fire_bed(tr.L, r.get("seed", 1), gain=0.4))
    tr.bus("bass", gain=0.75, reverb=0.1)
    tr.bus("pizz", gain=0.55, reverb=0.25)
    tr.bus("perc", gain=0.6, reverb=0.2)
    tr.bus("lead", gain=0.85, reverb=0.3)
    tr.bus("fx", gain=0.5, reverb=0.4)
    tr.bus("bed", gain=1.0, reverb=0.05)
    return tr.mix(ir=1.8, hi=12000)


def style_desert(r):
    """Sand and stone: hijaz scale on an oud, darbuka, a ney over a drone."""
    key, tr, rng, w = _setup(r, "hijaz", 96, seconds=48)
    root = key.note(0, 2)
    for bar in range(0, tr.bars, 2):
        tr.place("drone", tr.at(bar), drone(root, 2 * tr.bpb * tr.beat, bright=0.2, seed=bar))
        tr.place("drone", tr.at(bar), drone(root + 7, 2 * tr.bpb * tr.beat, bright=0.15, seed=bar + 1), gain=0.5)
    for bar in range(tr.bars):
        pat = (("doum", 0, 0.8), ("tek", 1, 0.5), ("tek", 1.5, 0.35), ("doum", 2, 0.7), ("tek", 3, 0.55), ("tek", 3.5, 0.3))
        for stroke, b, v in pat:
            tr.place("perc", tr.at(bar, b), darbuka(stroke, v, seed=bar * 8 + int(b * 2)), pan=0.45)
        for s, d in enumerate([0, 1, 2, 1, 0, 1, 4, 3] if bar % 2 == 0 else [4, 3, 1, 0, 1, 2, 1, 0]):
            if rng.random() < 0.75:
                tr.place("oud", tr.at(bar, s * 0.5), pluck(key.note(d, 3), 1.0, vel=0.6, brightness=0.45, decay=0.6, bend=0.004),
                         pan=0.35, humanize=0.006)
    for bar, bb, nt, d in melody(key, [[key.note(0, 3), key.note(2, 3), key.note(4, 3)]], tr.bars // 2, 4, rng,
                                 octave=5, density=0.45, start_bar=tr.bars // 2):
        tr.place("ney", tr.at(bar, bb), flute(nt, d * tr.beat * 0.95, vel=0.8, breath=0.35, scoop=0.02, seed=bar), pan=0.6)
    if "war" in w:
        for bar in range(tr.bars):
            tr.place("perc", tr.at(bar, 0), taiko(0.7, seed=bar))
            if bar % 2:
                tr.place("perc", tr.at(bar, 2), taiko(0.55, seed=bar + 50))
    if "alien" in w:
        for bar in range(0, tr.bars, 2):
            tr.place("fx", tr.at(bar, 1), theremin_line([(0, key.note(4, 5), 2), (2, key.note(6, 5), 2)], tr.beat, vel=0.6), pan=0.7)
        tr.bed("bed", wind_bed(tr.L, r.get("seed", 1), gain=0.25))
    tr.bus("drone", gain=0.7, reverb=0.4)
    tr.bus("perc", gain=0.8, reverb=0.25)
    tr.bus("oud", gain=0.8, reverb=0.3)
    tr.bus("ney", gain=0.85, reverb=0.5)
    tr.bus("fx", gain=0.6, reverb=0.7)
    tr.bus("bed", gain=1.0, reverb=0.05)
    return tr.mix(ir=2.8, hi=11000)


def style_ice(r):
    """Frost: glass bells high up, a cold pad, wind across snow."""
    key, tr, rng, w = _setup(r, "lydian", 66, seconds=50)
    ch = progression(key, [0, 1, 0, 4], 3, size=4)
    for bar in range(tr.bars):
        c = ch[bar % len(ch)]
        for j, nt in enumerate(c):
            tr.place("pad", tr.at(bar), pad(nt + 12, tr.bpb * tr.beat, attack=1.5, release=2.0, bright=0.5, detune=5, seed=bar * 4 + j))
        for s in range(8):
            if rng.random() < 0.6:
                tr.place("glass", tr.at(bar, s * 0.5), bell(int(rng.choice(c)) + 36, 2.5, vel=0.35, ratio=4.0, decay=1.2),
                         pan=rng.uniform(0.15, 0.85))
    for bar, bb, nt, d in melody(key, ch, tr.bars // 2, 4, rng, octave=5, density=0.3, start_bar=tr.bars // 2):
        tr.place("lead", tr.at(bar, bb), flute(nt, d * tr.beat * 0.95, vel=0.6, breath=0.2, seed=bar), pan=0.55)
    tr.bed("bed", wind_bed(tr.L, r.get("seed", 1), gain=0.4))
    tr.bus("pad", gain=0.7, reverb=0.6)
    tr.bus("glass", gain=0.7, reverb=0.8)
    tr.bus("lead", gain=0.8, reverb=0.6)
    tr.bus("bed", gain=1.0, reverb=0.1)
    return tr.mix(ir=4.0, wet=1.2, hi=12000, rms_db=-22.0)


def style_shanty(r):
    """At sea: an accordion, stomps and claps in 6/8, the swell underneath."""
    key, tr, rng, w = _setup(r, "dorian", 200, bpb=6, multiple=8)
    ch = progression(key, [0, 0, 6, 6, 0, 0, 4, 0], 3)
    for bar in range(tr.bars):
        c = ch[bar % len(ch)]
        tr.place("acc", tr.at(bar, 0), reed(c[0] - 12, 2 * tr.beat, vel=0.9), pan=0.4)
        tr.place("acc", tr.at(bar, 3), reed(c[0] - 12 + 7, 2 * tr.beat, vel=0.8), pan=0.4)
        for b in (1, 2, 4, 5):
            for nt in c:
                tr.place("acc", tr.at(bar, b), reed(nt + 12, 0.8 * tr.beat, vel=0.4, musette=12), pan=0.45)
        tr.place("perc", tr.at(bar, 0), kick(0.8)); tr.place("perc", tr.at(bar, 3), kick(0.7))
        tr.place("perc", tr.at(bar, 3), clap(0.5, seed=bar))
    for bar, bb, nt, d in melody(key, ch, tr.bars, 6, rng, octave=5, density=0.5):
        tr.place("lead", tr.at(bar, bb), reed(nt, d * tr.beat * 0.92, vel=0.8, musette=14), pan=0.6)
    tr.bed("bed", surf_bed(tr.L, max(1, tr.bars // 4), r.get("seed", 1), gain=0.3))
    tr.bus("acc", gain=0.75, reverb=0.25)
    tr.bus("perc", gain=0.7, reverb=0.2)
    tr.bus("lead", gain=0.85, reverb=0.3)
    tr.bus("bed", gain=1.0, reverb=0.05)
    return tr.mix(ir=2.0, hi=11000)


def style_hearth(r):
    """Borb's Lair: a lo-fi evening by the fire. Rhodes, brushed drums, a warm
    bass, the fire itself, a music-box tune, and now and then the borb croaks.

    Twice the length of the others with an A and a B section, so the one theme
    the client is named after does not repeat itself every minute."""
    key = Key("D", "major")
    tr = Track(bpm=72, bars=24, seed=r.get("seed", 11))
    a = [("D3", (0, 4, 11, 14)), ("B2", (0, 3, 10, 14)), ("E3", (0, 3, 10, 14)), ("A2", (0, 4, 10, 14))]
    b = [("G2", (0, 4, 11, 14)), ("F#2", (0, 3, 10, 14)), ("E3", (0, 3, 10, 14)), ("A2", (0, 4, 10, 17))]
    plan = a * 2 + b * 2 + a * 2
    swing = 0.09
    rng = np.random.default_rng(5)

    def m(name):
        return 12 * (int(name[-1]) + 1) + NOTE[name[:-1]]

    for bar, (root, shape) in enumerate(plan):
        notes = [m(root) + s for s in shape]
        for j, nt in enumerate(notes):
            tr.place("keys", tr.at(bar, 0) + 0.012 * j, epiano(nt + 12, 2.6, vel=0.75), pan=0.35 + 0.1 * j, humanize=0.004)
            tr.place("keys", tr.at(bar, 1.5 + swing) + 0.01 * j, epiano(nt + 12, 1.4, vel=0.45), pan=0.65 - 0.1 * j, humanize=0.004)
        r0 = m(root)
        tr.place("bass", tr.at(bar, 0), bass(r0, 1.6, vel=0.85))
        tr.place("bass", tr.at(bar, 2.5 + swing), bass(r0 + 7, 0.5, vel=0.6))
        tr.place("bass", tr.at(bar, 3), bass(r0, 0.8, vel=0.7))
        if bar >= 1:
            tr.place("drums", tr.at(bar, 0), kick(0.7))
            tr.place("drums", tr.at(bar, 2.5 + swing), kick(0.45))
            tr.place("drums", tr.at(bar, 1), rim(0.55, seed=bar))
            tr.place("drums", tr.at(bar, 3), rim(0.6, seed=bar + 50))
            for k in range(8):
                beat = k * 0.5 + (swing if k % 2 else 0)
                tr.place("drums", tr.at(bar, beat), hat(0.5 if k % 2 else 0.3, seed=bar * 8 + k), pan=0.62, humanize=0.003)
    tune_a = [(0, "A5", 1.0), (1, "F#5", 0.5), (1.5, "E5", 0.5), (2, "D5", 1.5),
              (4, "B4", 0.5), (4.5, "D5", 0.5), (5, "F#5", 1.0), (6, "E5", 2.0),
              (8, "G5", 1.0), (9, "F#5", 0.5), (9.5, "E5", 0.5), (10, "B4", 1.5),
              (12, "C#5", 0.5), (12.5, "E5", 0.5), (13, "A5", 1.0), (14, "G5", 0.5), (14.5, "E5", 1.5)]
    tune_b = [(0, "B5", 1.5), (1.5, "A5", 0.5), (2, "G5", 1.0), (3, "D5", 1.0),
              (4, "A5", 1.0), (5, "F#5", 0.5), (5.5, "E5", 0.5), (6, "C#5", 2.0),
              (8, "B4", 0.5), (8.5, "D5", 0.5), (9, "G5", 1.0), (10, "F#5", 1.0), (11, "E5", 1.0),
              (12, "E5", 0.5), (12.5, "F#5", 0.5), (13, "G5", 1.0), (14, "A5", 2.0)]
    for start, tune in ((4, tune_a), (8, tune_b), (12, tune_b), (20, tune_a)):
        for beat, note, _ in tune:
            tr.place("tune", tr.at(start, beat), musicbox(m(note), vel=0.55), pan=0.55, humanize=0.003)
    for i, (bar, beat) in enumerate(((2, 3.5), (5, 1.75), (7, 3.25), (10, 2.75), (14, 3.5), (17, 1.25), (19, 3.5), (22, 2.25))):
        tr.place("borb", tr.at(bar, beat), croak(vel=0.6, seed=i, pitch=0.9 + 0.05 * (i % 4)), pan=0.3 + 0.07 * i)
    tr.bed("fire", fire_bed(tr.L, 3, gain=1.0))
    tr.bus("fire", gain=0.22, reverb=0.05)
    tr.bus("keys", gain=1.0, reverb=0.3)
    tr.bus("bass", gain=0.9, reverb=0.03)
    tr.bus("drums", gain=0.75, reverb=0.12)
    tr.bus("tune", gain=0.8, reverb=0.45)
    tr.bus("borb", gain=0.7, reverb=0.5)
    return tr.mix(ir=1.8, hi=9000)


STYLES = {
    "ominous": style_ominous, "twisted_carol": style_twisted_carol, "festive": style_festive,
    "halloween": style_halloween, "synthwave": style_synthwave, "cyber": style_cyber, "lab": style_lab,
    "chiptune": style_chiptune, "space": style_space, "underwater": style_underwater, "epic": style_epic,
    "eastern": style_eastern, "forest": style_forest, "quirky": style_quirky, "desert": style_desert,
    "ice": style_ice, "shanty": style_shanty, "hearth": style_hearth,
}


# ---------------------------------------------------------------------------
# checks, output, theme wiring
# ---------------------------------------------------------------------------

def check_loop(x):
    steps = np.abs(np.diff(x, axis=0)).max(axis=1)
    return float(np.abs(x[0] - x[-1]).max()), float(np.percentile(steps, 99.9))


def describe(x):
    return {"seconds": round(x.shape[0] / SR, 1),
            "peak_dbfs": round(20 * np.log10(np.abs(x).max()), 1),
            "rms_dbfs": round(20 * np.log10(np.sqrt(np.mean(x ** 2))), 1)}


def render(recipe, ogg_path, preview_path=None, quality=0.35):
    import soundfile as sf
    x = STYLES[recipe["style"]](recipe)
    seam, typical = check_loop(x)
    if seam > typical:
        raise SystemExit(f"loop seam step {seam:.4f} > typical step {typical:.4f} - would click")
    if not np.all(np.isfinite(x)):
        raise SystemExit("non-finite samples")
    os.makedirs(os.path.dirname(os.path.abspath(ogg_path)), exist_ok=True)
    # One-second blocks: libsndfile 1.2.2's Vorbis writer segfaults when a single
    # write call carries more than about a minute of audio.
    data = x.astype(np.float32)
    with sf.SoundFile(ogg_path, "w", SR, 2, format="OGG", subtype="VORBIS",
                      compression_level=1.0 - quality) as out:
        for i in range(0, data.shape[0], SR):
            out.write(data[i:i + SR])
    info = describe(x)
    info["kib"] = round(os.path.getsize(ogg_path) / 1024)
    if preview_path:
        import lameenc
        enc = lameenc.Encoder()
        enc.set_bit_rate(96)
        enc.set_in_sample_rate(SR)
        enc.set_channels(2)
        enc.set_quality(2)
        pcm = (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()
        with open(preview_path, "wb") as f:
            f.write(enc.encode(pcm) + enc.flush())
    return info


def apply_to_theme(theme_dir, volume):
    """Point theme.json at the track and bump the minor version."""
    path = os.path.join(theme_dir, "theme.json")
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    audio = doc.get("audio") or {}
    changed = audio.get("bgm") != TRACK_FILE or audio.get("volume") != volume
    audio["bgm"] = TRACK_FILE
    audio["volume"] = volume
    doc["audio"] = audio
    if changed:
        parts = (str(doc.get("version", "1.0.0")).split(".") + ["0", "0"])[:3]
        try:
            doc["version"] = f"{int(parts[0])}.{int(parts[1]) + 1}.0"
        except ValueError:
            pass
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
        f.write("\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--theme", action="append", help="theme folder name (repeatable)")
    ap.add_argument("--all", action="store_true", help="every theme in music-recipes.json")
    ap.add_argument("--apply", action="store_true", help="also point theme.json at the track")
    ap.add_argument("--preview-dir", help="also write <theme>.mp3 previews here")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    with open(RECIPES, encoding="utf-8") as f:
        recipes = json.load(f)["themes"]
    if a.list:
        for name, rcp in recipes.items():
            print(f"{name:22} {rcp['style']:14} {rcp.get('key', '')} {rcp.get('mode', '')} {rcp.get('bpm', '')} "
                  f"{' '.join(rcp.get('with', []))}")
        return
    names = list(recipes) if a.all else (a.theme or [])
    if not names:
        ap.error("name a --theme, or use --all")
    failed = []
    for name in names:
        rcp = recipes.get(name)
        if not rcp:
            print(f"{name}: no recipe", file=sys.stderr); failed.append(name); continue
        tdir = os.path.join(ROOT, "themes", name)
        if not os.path.isdir(tdir):
            print(f"{name}: no such theme folder", file=sys.stderr); failed.append(name); continue
        prev = os.path.join(a.preview_dir, f"{name}.mp3") if a.preview_dir else None
        try:
            info = render(rcp, os.path.join(tdir, TRACK_FILE), prev)
        except (SystemExit, Exception) as e:      # one bad recipe must not stop the batch
            print(f"{name}: {type(e).__name__}: {e}", file=sys.stderr); failed.append(name); continue
        if a.apply:
            apply_to_theme(tdir, rcp.get("volume", 0.5))
        print(f"{name:22} {rcp['style']:14} {info}", flush=True)
    if failed:
        sys.exit(f"{len(failed)} failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()
