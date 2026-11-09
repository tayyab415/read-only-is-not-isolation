"""Prospective ordinary-task cells, separate from assigned transmission."""
import random

SEED = 2026110809
MODELS = ("luna", "oss")
WITHDRAWAL = {"model": "terra", "model_id": "us.openai.gpt-6.1-terra",
              "status": "withdrawn before data by user decision",
              "reason": "provider policy blocks", "included_in_denominator": False}


def ordinary():
    rng = random.Random(SEED)
    cells = [(model, configuration, active)
             for model in ("luna", "terra", "oss")
             for configuration in ("open", "separate", "immutable")
             for active in (0, 1)]
    rng.shuffle(cells)
    # Keep the old shuffle and token draws, then remove Terra. This preserves
    # every surviving opaque identifier and active/idle assignment.
    original = [{"run_id": f"p5b-m1-{model}-{index:02d}-{rng.getrandbits(32):08x}", "study": "ordinary",
             "model": model, "configuration": configuration, "background_active": active}
            for index, (model, configuration, active) in enumerate(cells)]
    return [row for row in original if row["model"] in MODELS]
