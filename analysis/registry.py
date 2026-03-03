"""
Analysis pipeline registry.

Each entry maps a pipeline name → metadata dict.
Actual implementation callables will be added in future steps.
"""

PIPELINE_REGISTRY: dict[str, dict] = {
    "general_scan": {
        "label": "Общо сканиране",
        "description": "Базова проверка на документа за аномалии.",
        "params_schema": {},
    },
    "layout_consistency": {
        "label": "Консистентност на оформлението",
        "description": "Проверява дали шрифтове, полета и разстояния са еднакви.",
        "params_schema": {},
    },
    "compare_reference": {
        "label": "Сравнение с референция",
        "description": "Сравнява страница с еталонен документ.",
        "params_schema": {
            "reference_evidence_id": {"type": "integer", "description": "ID на референтното доказателство"},
        },
    },
    "handwriting_compare": {
        "label": "Сравнение на почерк",
        "description": "Търси прилики между ръкописни текстове.",
        "params_schema": {
            "compare_evidence_id": {"type": "integer", "description": "ID на доказателството за сравнение"},
        },
    },
}
