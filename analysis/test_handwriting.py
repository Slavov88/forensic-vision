import os
import cv2
import numpy as np
from django.test import TestCase
from analysis.pipelines.handwriting_compare import run_handwriting_compare

class HandwritingCompareTestCase(TestCase):
    
    def setUp(self):
        self.test_dir = os.path.join(os.path.dirname(__file__), 'test_data_handwriting')
        os.makedirs(self.test_dir, exist_ok=True)
        
    def _create_clean_handwriting(self, path):
        # Create clear components (High Confidence scenario)
        img = 255 * np.ones((600, 600, 3), dtype=np.uint8)
        # Draw 40 "characters" to pass the >35 components threshold
        for i in range(8):
            for j in range(5):
                x = 50 + i * 60
                y = 100 + j * 80
                # Draw "slant" strokes
                cv2.line(img, (x, y), (x + 20, y + 40), (0, 0, 0), 2)
                cv2.ellipse(img, (x+10, y+20), (10, 15), 15, 0, 360, (0, 0, 0), 2)
                # Baseline reference
                cv2.line(img, (x, y+45), (x + 40, y + 45), (0, 0, 0), 1)
        cv2.imwrite(path, img)
        return path
        
    def _create_noisy_handwriting(self, path):
        # Sparse, noisy, very few components (Low Confidence scenario)
        img = 255 * np.ones((600, 600, 3), dtype=np.uint8)
        # Draw only 5 components (fails ROI quality and component count minimums)
        for i in range(4):
            cv2.line(img, (100 + i*40, 100), (120 + i*40, 150), (100, 100, 100), 2)
        cv2.imwrite(path, img)
        return path

    def test_handwriting_high_confidence(self):
        img_target = os.path.join(self.test_dir, 'hw_clean_t.png')
        img_ref = os.path.join(self.test_dir, 'hw_clean_r.png')
        self._create_clean_handwriting(img_target)
        self._create_clean_handwriting(img_ref)
        
        metrics, overlays = run_handwriting_compare(img_target, img_ref, 3001, {})
        
        self.assertEqual(metrics["pipeline"], "handwriting_compare")
        self.assertEqual(metrics["summary"]["confidence"], "HIGH")
        # Same image generator, similarity should be very high
        self.assertGreater(metrics["summary"]["similarity_score"], 95.0)
        
    def test_handwriting_low_confidence(self):
        img_target = os.path.join(self.test_dir, 'hw_noisy_t.png')
        img_ref = os.path.join(self.test_dir, 'hw_noisy_r.png')
        self._create_noisy_handwriting(img_target)
        self._create_noisy_handwriting(img_ref)
        
        metrics, overlays = run_handwriting_compare(img_target, img_ref, 3002, {})
        
        # Too few components -> LOW confidence
        self.assertEqual(metrics["summary"]["confidence"], "LOW")
        
        # Look for baseline
        baseline_diffs = [f for f in metrics["features"] if "Основната Линия" in f["metric"]]
        self.assertTrue(len(baseline_diffs) > 0, "Baseline should be in features.")
        baseline_diff = baseline_diffs[0]
        
        self.assertEqual(baseline_diff["target_val"], "Н/Д")
        self.assertEqual(baseline_diff["reliability"], "LOW")

    def tearDown(self):
        import shutil
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)
