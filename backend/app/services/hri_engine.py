"""
HRI (Home Run Intelligence) scoring engine.

Deterministic weighted-sum model:

    HRI = 100 * (0.50 * batter_power + 0.30 * pitcher_weakness + 0.20 * environment)
    HR Probability = HRI_raw * 0.45   (realistic ceiling of ~45%)

All sub-scores are normalized to [0,1] before weighting, so HRI is always in
[0,100] and probability always in [0,1]. Simple and explainable on purpose --
can be swapped for a trained ML model later without changing the signature.
"""
from dataclasses import dataclass


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


@dataclass
class BatterInput:
    barrel_rate: float       # 0.00 - 0.35 typical
    hard_hit_rate: float     # 0.00 - 0.70 typical
    xslg: float              # 0.300 - 0.750 typical
    exit_velocity: float     # 85 - 116 mph typical


@dataclass
class PitcherInput:
    hr_per9: float           # 0.0 - 3.0 typical
    flyball_rate: float      # 0.15 - 0.55 typical


@dataclass
class EnvironmentInput:
    wind: float              # -20 to +20 mph, positive = blowing out
    temperature: float       # 30 - 105 F
    park_factor: float       # 0.7 - 1.3 (1.0 = neutral)


def _batter_power_score(b: BatterInput) -> float:
    exit_velo_norm = _clamp((b.exit_velocity - 85) / 30)
    barrel_norm = _clamp(b.barrel_rate / 0.30)
    hard_hit_norm = _clamp(b.hard_hit_rate / 0.65)
    xslg_norm = _clamp((b.xslg - 0.300) / 0.450)
    return barrel_norm * 0.35 + hard_hit_norm * 0.25 + xslg_norm * 0.25 + exit_velo_norm * 0.15


def _pitcher_weakness_score(p: PitcherInput) -> float:
    hr9_norm = _clamp(p.hr_per9 / 3.0)
    flyball_norm = _clamp(p.flyball_rate / 0.55)
    return hr9_norm * 0.6 + flyball_norm * 0.4


def _environment_score(e: EnvironmentInput) -> float:
    wind_norm = _clamp((e.wind + 20) / 40)
    temp_norm = _clamp((e.temperature - 30) / 75)
    park_norm = _clamp((e.park_factor - 0.7) / 0.6)
    return wind_norm * 0.35 + temp_norm * 0.25 + park_norm * 0.40


def calculate_hri(batter: BatterInput, pitcher: PitcherInput, environment: EnvironmentInput) -> dict:
    batter_power = _batter_power_score(batter)
    pitcher_weakness = _pitcher_weakness_score(pitcher)
    env_score = _environment_score(environment)

    hri_raw = _clamp(batter_power * 0.50 + pitcher_weakness * 0.30 + env_score * 0.20)

    return {
        "hri_score": round(hri_raw * 100, 1),
        "hr_probability": round(_clamp(hri_raw * 0.45), 3),
        "components": {
            "batter_power": round(batter_power, 3),
            "pitcher_weakness": round(pitcher_weakness, 3),
            "environment_score": round(env_score, 3),
        },
    }


def calculate_hri_from_dicts(batter: dict, pitcher: dict, environment: dict) -> dict:
    """Convenience wrapper accepting raw dicts as returned by the services."""
    return calculate_hri(
        BatterInput(
            barrel_rate=batter["barrel_rate"],
            hard_hit_rate=batter["hard_hit_rate"],
            xslg=batter["xslg"],
            exit_velocity=batter["exit_velocity"],
        ),
        PitcherInput(hr_per9=pitcher["hr_per9"], flyball_rate=pitcher["flyball_rate"]),
        EnvironmentInput(
            wind=environment["wind"],
            temperature=environment["temperature"],
            park_factor=environment["park_factor"],
        ),
    )
