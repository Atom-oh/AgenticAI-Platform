"""Candidate metadata only; no image, model version or compatibility is inferred."""

MODEL_FAMILIES = {
    "qwen": {
        "label": "Qwen",
        "baseline_reference": "Qwen/Qwen3-8B",
        "approved_training_image": None,
        "approved_base_model_sha256": None,
        "requires_family_specific_trainer_approval": True,
    },
    "gemma4": {
        "label": "Gemma 4 (user-nominated candidate)",
        "baseline_reference": None,
        "approved_training_image": None,
        "approved_base_model_sha256": None,
        "requires_family_specific_trainer_approval": True,
    },
    "deepseek": {
        "label": "DeepSeek",
        "baseline_reference": None,
        "approved_training_image": None,
        "approved_base_model_sha256": None,
        "requires_family_specific_trainer_approval": True,
    },
}
