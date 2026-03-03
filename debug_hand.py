import sys, os, cv2, numpy as np

sys.path.append('.')
from analysis.pipelines.handwriting_compare import run_handwriting_compare

path = 'debug_noisy.png'
metrics, overlays = run_handwriting_compare(path, path, "debug1", {})

features = metrics["features"]
found = any("Основна Линия" in f["metric"] for f in features)
print("HAS BASELINE:", found)
if not found:
    print("Metrics present:")
    for f in features:
        print(f["metric"])
