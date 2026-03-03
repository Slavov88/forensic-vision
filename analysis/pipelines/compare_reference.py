import os
import cv2
import numpy as np
from skimage.metrics import structural_similarity as ssim
from django.conf import settings

def preprocess_for_compare(img):
    """Normalize intensity and crop to content area."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
    gray = clahe.apply(gray)
    
    _, thresh = cv2.threshold(gray, 240, 255, cv2.THRESH_BINARY_INV)
    coords = cv2.findNonZero(thresh)
    if coords is not None:
        x, y, w, h = cv2.boundingRect(coords)
        cropped = gray[y:y+h, x:x+w]
        return cropped, (x, y, w, h)
    return gray, (0, 0, img.shape[1], img.shape[0])

def align_text_projections(target_gray, ref_gray):
    """Robust alignment for text pages using horizontal/vertical projections."""
    def get_projections(img):
        inv = 255 - img
        h_proj = np.sum(inv, axis=1)
        v_proj = np.sum(inv, axis=0)
        return h_proj, v_proj

    h_ref, v_ref = get_projections(ref_gray)
    h_tar, v_tar = get_projections(target_gray)
    
    def find_shift(p1, p2):
        corr = np.correlate(p1.astype(float), p2.astype(float), mode='same')
        return np.argmax(corr) - (len(p1) // 2)

    dy = find_shift(h_ref, h_tar)
    dx = find_shift(v_ref, v_tar)
    
    M = np.float32([[1, 0, dx], [0, 1, dy]])
    return M, float(dx), float(dy)

def run_compare_reference(target_path, ref_path, job_id, params=None):
    """
    Overhauled compare_reference: Dual-mode alignment, confidence scoring,
    and robust diff filtering.
    """
    params = params or {}
    target = cv2.imread(target_path)
    ref = cv2.imread(ref_path)
    if target is None or ref is None:
        raise ValueError("Could not read target or reference image")

    target_gray_full, t_crop = preprocess_for_compare(target)
    ref_gray_full, r_crop = preprocess_for_compare(ref)
    
    target_h, target_w = target_gray_full.shape[:2]
    common_h = 2000
    common_w = int(target_w * (common_h / target_h))
    
    target_gray = cv2.resize(target_gray_full, (common_w, common_h))
    ref_gray = cv2.resize(ref_gray_full, (common_w, common_h))
    
    text_density = np.sum(target_gray < 240) / (common_h * common_w)
    is_text_page = text_density > 0.02

    alignment_method = "orb_ransac"
    confidence = "low"
    alignment_failed = False
    M_final = np.eye(2, 3, dtype=np.float32)
    ecc_corr = 0.0
    inliers = 0

    if is_text_page:
        # Step A: Projection shift
        M_proj, dx, dy = align_text_projections(target_gray, ref_gray)
        alignment_method = "text_projections"
        
        # Step B: Refine with ECC (subset of image for speed)
        try:
            criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 50, 0.001)
            ecc_corr, M_ecc = cv2.findTransformECC(ref_gray, target_gray, M_proj, cv2.MOTION_TRANSLATION, criteria)
            M_final = M_ecc
            alignment_method = "text_projection+ecc"
        except Exception:
            M_final = M_proj
    else:
        orb = cv2.ORB_create(5000)
        kp1, des1 = orb.detectAndCompute(target_gray, None)
        kp2, des2 = orb.detectAndCompute(ref_gray, None)
        
        if des1 is not None and des2 is not None:
            bf = cv2.BFMatcher(cv2.NORM_HAMMING)
            matches = bf.knnMatch(des1, des2, k=2)
            good = [m for m, n in matches if m.distance < 0.75 * n.distance]
            
            if len(good) > 15:
                src_pts = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
                dst_pts = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
                
                H, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
                if H is not None:
                    inliers = int(np.sum(mask))
                    M_final = H[:2, :]
                    alignment_method = "orb_ransac"
                else:
                    alignment_failed = True
            else:
                alignment_failed = True
        else:
            alignment_failed = True

    aligned_target = cv2.warpAffine(target_gray, M_final, (common_w, common_h), borderValue=255)
    
    corr_matrix = cv2.matchTemplate(aligned_target, ref_gray, cv2.TM_CCOEFF_NORMED)
    global_corr = float(np.max(corr_matrix))
    
    if global_corr > 0.95: confidence = "high"
    elif global_corr > 0.85: confidence = "medium"
    else: 
        confidence = "low"
        if global_corr < 0.7: alignment_failed = True

    score, diff_map = ssim(ref_gray, aligned_target, full=True)
    diff_img = (diff_map * 255).astype("uint8")
    
    bboxes = []
    # If alignment is low/failed, DO NOT show bboxes (anti-false positive)
    if not alignment_failed and confidence != "low":
        thresh = cv2.threshold(diff_img, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)
        
        cnts, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in cnts:
            area = cv2.contourArea(c)
            # Filter: area ratio > 0.05% of common image
            area_ratio = area / (common_h * common_w)
            if area_ratio < 0.0005 or area_ratio > 0.4: continue
            
            x, y, w, h = cv2.boundingRect(c)
            # Map back to original target coords (approximation)
            scale_y = target_h / common_h
            scale_x = target_w / common_w
            rx, ry, rw, rh = int((x)*scale_x + t_crop[0]), int((y)*scale_y + t_crop[1]), int(w*scale_x), int(h*scale_y)
            bboxes.append({"bbox": [rx, ry, rw, rh], "area_ratio": round(area_ratio*100, 3)})

    # 5. Visual Overlays
    # Diff Heatmap
    diff_norm = cv2.normalize(255 - diff_img, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    diff_heatmap = cv2.applyColorMap(diff_norm, cv2.COLORMAP_JET)
    diff_heatmap_full = cv2.resize(diff_heatmap, (ref.shape[1], ref.shape[0]))
    heatmap_blended = cv2.addWeighted(ref, 0.7, diff_heatmap_full, 0.3, 0)
    
    # Change BBoxes
    change_overlay = ref.copy()
    for b in bboxes:
        bx, by, bw, bh = b["bbox"]
        cv2.rectangle(change_overlay, (bx, by), (bx+bw, by+bh), (0, 0, 255), 2)

    # Persistence
    rel_dir = os.path.join('artifacts', f'job_{job_id}')
    abs_dir = os.path.join(settings.MEDIA_ROOT, rel_dir)
    os.makedirs(abs_dir, exist_ok=True)
    
    paths = {
        "diff": os.path.join(rel_dir, 'diff_heatmap.png'),
        "changes": os.path.join(rel_dir, 'change_overlay.png'),
        "aligned": os.path.join(rel_dir, 'aligned_target.png')
    }
    cv2.imwrite(os.path.join(settings.MEDIA_ROOT, paths["diff"]), heatmap_blended)
    cv2.imwrite(os.path.join(settings.MEDIA_ROOT, paths["changes"]), change_overlay)
    cv2.imwrite(os.path.join(settings.MEDIA_ROOT, paths["aligned"]), cv2.resize(aligned_target, (ref.shape[1], ref.shape[0])))

    metrics = {
        "ssim_score": round(float(score), 4),
        "global_corr": round(float(global_corr), 4),
        "confidence": str(confidence),
        "alignment_failed": bool(alignment_failed),
        "alignment_method": str(alignment_method),
        "found_changes": len(bboxes),
        "inliers": int(inliers),
        "is_text_page": bool(is_text_page),
        "warning": "Ниска надеждност на подравняването." if alignment_failed or confidence == "low" else None
    }
    
    overlays = [
        {"kind": "overlay", "file": paths["diff"], "label": "Топлинна карта на разликите"},
        {"kind": "overlay", "file": paths["changes"], "label": "Локализирани промени"}
    ]
    if not alignment_failed:
        overlays.append({"kind": "overlay", "file": paths["aligned"], "label": "Подравнен документ"})

    return metrics, overlays
