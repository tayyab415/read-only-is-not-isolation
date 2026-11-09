"""Prospective ordered model-subject schedule."""
import random

SEED = 2026110808
MODELS = ("luna", "oss")
CONFIGURATIONS = ("open", "separate", "immutable")
WITHDRAWAL = {"model": "terra", "model_id": "us.openai.gpt-6.1-terra",
              "status": "withdrawn before data by user decision",
              "reason": "provider policy blocks", "included_in_denominator": False}


def assigned():
    rng = random.Random(SEED)
    result = []
    # Consume the original RNG allocations before filtering the withdrawn
    # subject. Surviving values, tokens and ordering stay exactly as frozen.
    for model in ("luna", "terra", "oss"):
        configurations = list(CONFIGURATIONS)
        rng.shuffle(configurations)
        for configuration in configurations:
            result.append({"run_id": f"p4-m1-{model}-{configuration}", "model": model,
                           "configuration": configuration, "study": "assigned",
                           "values": {actor: rng.randrange(16) for actor in ("alice", "bob")},
                           "public_token": f"{rng.getrandbits(64):016x}"})
    return [row for row in result if row["model"] in MODELS]
