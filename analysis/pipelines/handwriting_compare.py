import os
import cv2
import numpy as np
import logging
import math
from django.conf import settings

logger = logging.getLogger(__name__)

def run_handwriting_compare(target_path, ref_path, job_id, params=None):
    """
    Calibrated Document-vs-Document Handwriting Comparison.
    Produces a similarity score (0-100), detailed feature comparisons with reliability metrics,
    and a defensible high/medium/low confidence rating based on ROI and segmentation quality.
    """
    params = params or {}
    
    # 1. Feature Extraction (ROI-first)
    t_feat, t_img, t_overlay = _extract_handwriting_features(target_path, box_param=params.get('roi'))
    # For reference, usually no ROI is provided in current UI, so it auto-detects
    r_feat, r_img, r_overlay = _extract_handwriting_features(ref_path, box_param=None)
    
    # 2. Compare Features with Reliability
    diffs = {}
    dist_sum = 0.0
    weight_sum = 0.0
    
    # Define features, base weight, and their logical grouping
    feature_specs = {
        "slant_angle_deg": {"weight": 1.5, "max_diff": 45.0, "label": "Среден Наклон (Slant)", "group": "geometry"},
        "baseline_angle_deg": {"weight": 1.0, "max_diff": 20.0, "label": "Ъгъл на Основната Линия", "group": "geometry"},
        "stroke_density": {"weight": 0.8, "max_diff": 0.3, "label": "Плътност на Мастилото", "group": "stroke"},
        "avg_component_height": {"weight": 1.2, "max_diff": 50.0, "label": "Средна Височина (px)", "group": "component"},
        "avg_component_width": {"weight": 1.0, "max_diff": 50.0, "label": "Средна Ширина (px)", "group": "component"},
        "contour_complexity": {"weight": 1.0, "max_diff": 5.0, "label": "Сложност на Контура", "group": "shape"},
    }
    
    rel_weights = {"HIGH": 1.0, "MEDIUM": 0.6, "LOW": 0.25}
    warnings = []
    groups = {"geometry": {"dist": 0.0, "w": 0.0}, "stroke": {"dist": 0.0, "w": 0.0}, "component": {"dist": 0.0, "w": 0.0}, "shape": {"dist": 0.0, "w": 0.0}}
    
    for key, spec in feature_specs.items():
        if key in t_feat and key in r_feat:
            t_data = t_feat[key]
            r_data = r_feat[key]
            
            # If either feature was fundamentally undetected/invalid
            if not t_data.get('detected', True) or not r_data.get('detected', True) or t_data['val'] is None or r_data['val'] is None:
                diffs[key] = {
                    "metric": spec["label"],
                    "target_val": "Н/Д",
                    "ref_val": "Н/Д",
                    "diff_val": "Н/Д",
                    "reliability": "LOW",
                    "reliability_color": "danger"
                }
                warnings.append(f"Неуспешно извличане на {spec['label']} поради лош входен сигнал.")
                continue
                
            val_t = float(t_data['val'])
            val_r = float(r_data['val'])
            diff = abs(val_t - val_r)
            
            t_rel = t_data.get('reliability', 'MEDIUM')
            r_rel = r_data.get('reliability', 'MEDIUM')
            if t_rel == "LOW" or r_rel == "LOW":
                feat_rel = "LOW"
            elif t_rel == "HIGH" and r_rel == "HIGH":
                feat_rel = "HIGH"
            else:
                feat_rel = "MEDIUM"
                
            rel_color = "danger" if feat_rel == "LOW" else ("success" if feat_rel == "HIGH" else "warning")
            
            diffs[key] = {
                "metric": spec["label"],
                "target_val": round(val_t, 2),
                "ref_val": round(val_r, 2),
                "diff_val": round(diff, 2),
                "reliability": feat_rel,
                "reliability_color": rel_color
            }
            
            
            norm_dist = min(1.0, diff / spec["max_diff"])
            effective_weight = spec["weight"] * rel_weights[feat_rel]
            
            dist_sum += norm_dist * effective_weight
            weight_sum += effective_weight
            
            groups[spec['group']]["dist"] += (1.0 - norm_dist) * 100 * effective_weight
            groups[spec['group']]["w"] += effective_weight
    
    if weight_sum > 0:
        overall_dist = dist_sum / weight_sum
        similarity_score = max(0.0, 100.0 * (1.0 - overall_dist))
    else:
        similarity_score = 0.0
        
    similarity_score = round(similarity_score, 1)
    
    
    score_breakdown = {}
    for g, g_data in groups.items():
        if g_data["w"] > 0:
            score_breakdown[g] = round(g_data["dist"] / g_data["w"], 1)
        else:
            score_breakdown[g] = "Н/Д"
            
    
    t_comps = t_feat.get("components_count", 0)
    r_comps = r_feat.get("components_count", 0)
    t_roi_q = t_feat.get("roi_quality", "LOW")
    r_roi_q = r_feat.get("roi_quality", "LOW")
    
    slant_diff = diffs.get("slant_angle_deg", {}).get("diff_val")
    slant_rel = diffs.get("slant_angle_deg", {}).get("reliability", "LOW")
    
    baseline_t = t_feat.get("baseline_angle_deg", {}).get("detected", False)
    baseline_r = r_feat.get("baseline_angle_deg", {}).get("detected", False)
    
    
    confidence = "HIGH"
    conf_reasons = []
    
    if t_comps < 15 or r_comps < 15:
        confidence = "LOW"
        conf_reasons.append(f"Твърде малко ръкописни компоненти (Цел: {t_comps}, Еталон: {r_comps}).")
    elif t_comps < 35 or r_comps < 35:
        confidence = "MEDIUM" if confidence == "HIGH" else confidence
        conf_reasons.append("Среден до малък брой компоненти.")
        
    if t_roi_q == "LOW" or r_roi_q == "LOW":
        confidence = "LOW"
        conf_reasons.append("Лошо качество на изолирания ръкописен регион (възможен прекомерен шум).")
        
    if not baseline_t or not baseline_r:
        confidence = "MEDIUM" if confidence == "HIGH" else confidence
        conf_reasons.append("Не са открити стабилни редове за основната линия.")
        
    if slant_diff != "Н/Д" and slant_diff > 25.0 and slant_rel != "HIGH":
        confidence = "LOW"
        conf_reasons.append("Голяма разлика в наклона, съчетана с ниска/средна надеждност на метриката.")
        
    if confidence == "HIGH":
        confidence_desc = "Силна надеждност на данните: стабилна сегментация, добри региони и достатъчен брой символи."
    elif confidence == "MEDIUM":
        confidence_desc = "Средна надеждност: " + "; ".join(conf_reasons)
    else:
        confidence_desc = "Ниска надеждност на изводите: " + "; ".join(conf_reasons)


    if similarity_score >= 75:
        consistency = "ВИСОКО СХОДСТВО"
        consistency_color = "success"
    elif similarity_score >= 50:
        consistency = "ЧАСТИЧНО СХОДСТВО (Възможни стилови/външни вариации)"
        consistency_color = "warning"
    else:
        consistency = "РАЗЛИЧЕН СТИЛ НА ИЗПИСВАНЕ"
        consistency_color = "danger"
        

    rel_dir = os.path.join('artifacts', f'job_{job_id}')
    abs_dir = os.path.join(settings.MEDIA_ROOT, rel_dir)
    os.makedirs(abs_dir, exist_ok=True)
    overlays_out = []
    
    if t_overlay is not None:
        t_overlay_name = f"handwriting_t_{job_id}.jpg"
        cv2.imwrite(os.path.join(abs_dir, t_overlay_name), t_overlay)
        overlays_out.append({"file": os.path.join(rel_dir, t_overlay_name).replace("\\", "/"), "label": "Рамкиран ROI (ЦЕЛ)"})
        
    if r_overlay is not None:
        r_overlay_name = f"handwriting_r_{job_id}.jpg"
        cv2.imwrite(os.path.join(abs_dir, r_overlay_name), r_overlay)
        overlays_out.append({"file": os.path.join(rel_dir, r_overlay_name).replace("\\", "/"), "label": "Рамкиран ROI (ЕТАЛОН)"})

    metrics = {
        "pipeline": "handwriting_compare",
        "pipeline_label": "Сравнение на почерк",
        "summary": {
            "similarity_score": similarity_score,
            "consistency": consistency,
            "consistency_color": consistency_color,
            "confidence": confidence,
            "confidence_desc": confidence_desc,
            "limitations": [
                "Резултатът е базиран на евристики (heuristics) и не представлява съдебно-графологична експертиза.",
                "Точността зависи силно от чистотата на сканирането и правилното изолиране (ROI) на ръкописа.",
                "Ръкописът може да варира естествено според настроение, пишещо средство или поза.",
            ],
            "warnings": warnings,
            "score_breakdown": score_breakdown
        },
        "features": list(diffs.values()),
        "target_stats": {
            "components_count": t_comps,
            "roi_quality": t_roi_q,
            "roi_used": t_feat.get("roi_used", False)
        },
        "reference_stats": {
            "components_count": r_comps,
            "roi_quality": r_roi_q,
            "roi_used": r_feat.get("roi_used", False)
        }
    }
    
    return metrics, overlays_out


def _auto_detect_handwriting_roi(img):
    """
    Attempts to find the largest block of text/ink in the image.
    Returns (x, y, w, h). If it fails drastically, it returns the full image extent.
    """
    original_h, original_w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (15, 15), 0)
    thresh = cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 15)
    
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 10)) # Connect horizontal lines
    dilated = cv2.dilate(thresh, kernel, iterations=2)
    
    contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    if not contours:
        return 0, 0, original_w, original_h
        

    contours = sorted(contours, key=cv2.contourArea, reverse=True)[:5]
    all_points = np.vstack(contours)
    x, y, w, h = cv2.boundingRect(all_points)
    

    pad = 20
    x = max(0, x - pad)
    y = max(0, y - pad)
    w = min(original_w - x, w + pad * 2)
    h = min(original_h - y, h + pad * 2)
    

    if w < 50 or h < 50:
        return 0, 0, original_w, original_h
        
    return x, y, w, h


def _extract_handwriting_features(img_path, box_param=None):
    img = cv2.imread(img_path)
    if img is None:
        return {}, None, None
        
    original_h, original_w = img.shape[:2]
    
    
    if box_param and box_param.get('x') is not None:
        x, y = int(box_param['x']), int(box_param['y'])
        w, h = int(box_param['width']), int(box_param['height'])
        roi_used = "Manual"
    else:
        x, y, w, h = _auto_detect_handwriting_roi(img)
        roi_used = "Auto"
        
    x = max(0, min(x, original_w-1))
    y = max(0, min(y, original_h-1))
    w = max(1, min(w, original_w - x))
    h = max(1, min(h, original_h - y))
    
    img_roi = img[y:y+h, x:x+w]
    gray = cv2.cvtColor(img_roi, cv2.COLOR_BGR2GRAY)
    
    
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    thresh = cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 21, 10)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    cleaned = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel, iterations=1)
    
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(cleaned, 8, cv2.CV_32S)
    
    overlay = img_roi.copy()
    
    valid_heights, valid_widths, valid_areas, valid_perims, angles = [], [], [], [], []
    ink_pixels = 0
    
    
    for i in range(1, num_labels):
        cx, cy, cw, ch, area = stats[i]
        
        
        if area < 10 or cw > original_w * 0.8 or ch > original_h * 0.6 or cw < 3 or ch < 3:
            continue
            
        ink_pixels += area
        valid_heights.append(ch)
        valid_widths.append(cw)
        valid_areas.append(area)
        
        component_mask = (labels == i).astype(np.uint8) * 255
        contours, _ = cv2.findContours(component_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        if contours:
            cnt = contours[0]
            perim = cv2.arcLength(cnt, True)
            valid_perims.append(perim)
            
            if len(cnt) > 4:
                center, (rw, rh), angle = cv2.minAreaRect(cnt)
                if rw < rh:
                    a = angle + 90 if angle < -45 else angle
                else:
                    a = angle
                
                if abs(a) > 5.0 and abs(a) < 85.0:
                    angles.append(a)
            
            cv2.drawContours(overlay, [cnt], -1, (0, 255, 0), 1)
            cv2.rectangle(overlay, (cx, cy), (cx+cw, cy+ch), (255, 0, 0), 1)

    components_count = len(valid_heights)
    
    
    ink_ratio = ink_pixels / (w * h) if w * h > 0 else 0
    if ink_ratio < 0.01 or ink_ratio > 0.4 or components_count < 5:
        roi_quality = "LOW"
    elif ink_ratio < 0.03 or components_count < 20:
        roi_quality = "MEDIUM"
    else:
        roi_quality = "HIGH"
        
    
    def make_feat(v, r, d=True): return {"val": v, "reliability": r, "detected": d}
    
    feat_h = np.mean(valid_heights) if valid_heights else 0.0
    feat_w = np.mean(valid_widths) if valid_widths else 0.0
    feat_density = ink_ratio
    feat_comp_rel = "LOW" if components_count < 10 else ("MEDIUM" if components_count < 30 else "HIGH")
    
    features = {
        "components_count": components_count,
        "roi_quality": roi_quality,
        "roi_used": roi_used,
        "avg_component_height": make_feat(feat_h, feat_comp_rel),
        "avg_component_width": make_feat(feat_w, feat_comp_rel),
        "stroke_density": make_feat(feat_density, roi_quality), 
    }
    
    complexities = [p / (math.sqrt(a) + 1e-5) for p, a in zip(valid_perims, valid_areas)] if valid_areas else []
    features["contour_complexity"] = make_feat(np.mean(complexities) if complexities else 0.0, feat_comp_rel)
    
    
    if len(angles) > 5:
        median_slant = np.median(angles)
        q75, q25 = np.percentile(angles, [75 ,25])
        iqr = q75 - q25
        
        
        if iqr > 40.0:
            slant_rel = "LOW"
        elif iqr > 25.0 or len(angles) < 15:
            slant_rel = "MEDIUM"
        else:
            slant_rel = "HIGH"
            
        features["slant_angle_deg"] = make_feat(median_slant, slant_rel)
    else:
        features["slant_angle_deg"] = make_feat(None, "LOW", False)
        
    
    edges = cv2.Canny(cleaned, 50, 150, apertureSize=3)
    lines = cv2.HoughLinesP(edges, 1, np.pi/180, threshold=50, minLineLength=w*0.2, maxLineGap=20)
    
    base_angles = []
    if lines is not None:
        for line in lines:
            x1, y1, x2, y2 = line[0]
            
            angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
            
            if abs(angle) < 25.0:
                base_angles.append(angle)
                cv2.line(overlay, (x1, y1), (x2, y2), (0, 0, 255), 2)
                
    if len(base_angles) > 1:
        base_median = np.median(base_angles)
        base_rel = "HIGH" if len(base_angles) > 4 else "MEDIUM"
        features["baseline_angle_deg"] = make_feat(base_median, base_rel)
    else:
        features["baseline_angle_deg"] = make_feat(None, "LOW", False)
        
    return features, img_roi, overlay
