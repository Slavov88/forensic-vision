import os
import cv2
import numpy as np
from django.conf import settings

def mad_outliers(data, threshold=3.5):
    """Calculate outliers using Median Absolute Deviation."""
    if not data:
        return np.array([])
    data = np.array(data)
    median = np.median(data)
    mad = np.median(np.abs(data - median))
    if mad == 0:
        mad = 1.0 # prevent division by zero, use a small constant if mad is 0 but we have variance?
        # Actually if mad is 0, any deviation > 0 is technically an outlier if we divide by mad.
        # Let's just use strict diff if mad is 0
        diff = np.abs(data - median)
        return diff > threshold * max(1.0, np.std(data) * 0.1) # fallback
    
    modified_z_scores = 0.6745 * (data - median) / mad
    return np.abs(modified_z_scores) > threshold

def _deskew(image):
    # Simple deskew using projection or Hough
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.bitwise_not(gray)
    thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]
    
    coords = np.column_stack(np.where(thresh > 0))
    if len(coords) < 100:
        return image, 0.0

    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle
        
    if abs(angle) < 0.5 or abs(angle) > 20: 
        return image, 0.0 # Don't rotate if too small or too chaotic
        
    (h, w) = image.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(image, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    return rotated, angle

def run_layout_consistency(target_path, job_id, params=None):
    params = params or {}
    img = cv2.imread(target_path)
    if img is None:
        raise ValueError("Could not read image")
        
    original_h, original_w = img.shape[:2]
    
    # 1. Preprocess
    # Deskew
    img_deskewed, angle = _deskew(img)
    
    gray = cv2.cvtColor(img_deskewed, cv2.COLOR_BGR2GRAY)
    
    
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 21, 10)
    
    
    text_density_pct = (np.count_nonzero(binary) / (original_w * original_h)) * 100
    text_page_detected = text_density_pct > 2.0
    
    
    horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (int(original_w / 30), 1))
    horizontal_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horizontal_kernel, iterations=2)
    
    
    vertical_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, int(original_h / 30)))
    vertical_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vertical_kernel, iterations=2)
    
    table_mask = cv2.add(horizontal_lines, vertical_lines)
    table_mask = cv2.dilate(table_mask, np.ones((5,5), np.uint8), iterations=3)
    
    
    text_mask = cv2.subtract(binary, table_mask)
    
    
    kernel_line = cv2.getStructuringElement(cv2.MORPH_RECT, (40, 2))
    lines_closed = cv2.morphologyEx(text_mask, cv2.MORPH_CLOSE, kernel_line)
    lines_closed = cv2.morphologyEx(lines_closed, cv2.MORPH_OPEN, np.ones((2,2), np.uint8))
    
    line_cnts, _ = cv2.findContours(lines_closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    lines = []
    line_heights = []
    line_lefts = []
    
    lines_overlay = img_deskewed.copy()
    
    for c in line_cnts:
        x, y, w, h = cv2.boundingRect(c)
        
        if h < 8 or h > 100 or w < 20: 
            continue
            
        
        overlap_table = np.count_nonzero(table_mask[y:y+h, x:x+w]) / (w*h)
        if overlap_table > 0.3:
            continue
            
        lines.append({"bbox": [x, y, w, h], "cx": x + w/2, "cy": y + h/2})
        line_heights.append(h)
        line_lefts.append(x)
        
        cv2.rectangle(lines_overlay, (x, y), (x+w, y+h), (255, 200, 0), 1)

    
    kernel_block = cv2.getStructuringElement(cv2.MORPH_RECT, (50, 40))
    blocks_closed = cv2.morphologyEx(lines_closed, cv2.MORPH_CLOSE, kernel_block)
    block_cnts, _ = cv2.findContours(blocks_closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    blocks = []
    blocks_overlay = img_deskewed.copy()
    
    for c in block_cnts:
        x, y, w, h = cv2.boundingRect(c)
        if w < 50 or h < 20: continue
        blocks.append([x, y, w, h])
        cv2.rectangle(blocks_overlay, (x, y), (x+w, y+h), (0, 255, 0), 2)

    
    for l in lines:
        l["block_idx"] = -1
        lx, ly, lw, lh = l["bbox"]
        lcx, lcy = l["cx"], l["cy"]
        best_overlap = 0
        best_b_idx = -1
        
        for b_idx, b in enumerate(blocks):
            bx, by, bw, bh = b
            
            overlap_x = max(0, min(lx+lw, bx+bw) - max(lx, bx))
            overlap_y = max(0, min(ly+lh, by+bh) - max(ly, by))
            area = overlap_x * overlap_y
            if area > best_overlap:
                best_overlap = area
                best_b_idx = b_idx
                
        if best_overlap > (lw*lh) * 0.5 or (best_b_idx != -1 and blocks[best_b_idx][0] <= lcx <= blocks[best_b_idx][0]+blocks[best_b_idx][2] and blocks[best_b_idx][1] <= lcy <= blocks[best_b_idx][1]+blocks[best_b_idx][3]):
            l["block_idx"] = best_b_idx

    # Grouping & Block Analysis
    block_types = {}
    line_subtypes = {}
    title_like_blocks = 0
    body_like_blocks = 0
    
    for b_idx, b in enumerate(blocks):
        bx, by, bw, bh = b
        b_lines = [l for l in lines if l["block_idx"] == b_idx]
        b_lines.sort(key=lambda l: l["bbox"][1])
        
        
        overlap_table = np.count_nonzero(table_mask[by:by+bh, bx:bx+bw]) / (bw*bh)
        if overlap_table > 0.15:
            block_types[b_idx] = "table"
            continue
            
        if len(b_lines) == 0:
            block_types[b_idx] = "unknown"
            continue
            
        
        avg_left_margin = np.mean([l["bbox"][0] for l in b_lines])
        avg_right_margin = original_w - np.mean([l["bbox"][0] + l["bbox"][2] for l in b_lines])
        center_diff = abs(avg_left_margin - avg_right_margin)
        
        if len(b_lines) <= 3 and center_diff < original_w * 0.1 and avg_left_margin > original_w * 0.15:
            block_types[b_idx] = "title"
            title_like_blocks += 1
            continue
            
        
        if len(b_lines) > 1 and bw < original_w * 0.5:
            lefts = np.array([l["bbox"][0] for l in b_lines])
            if np.std(lefts) < 10 and avg_left_margin > original_w * 0.1:
                block_types[b_idx] = "list"
                continue
                
        block_types[b_idx] = "body"
        body_like_blocks += 1
        
        
        for i, l in enumerate(b_lines):
            l_idx = lines.index(l)
            lx, ly, lw, lh = l["bbox"]
            left_offset = lx - bx
            
            subtype = "continuation_line"
            if i == 0 and left_offset > 15 and left_offset < original_w * 0.1:
                subtype = "paragraph_first_line"
            elif left_offset > 15 and lx > avg_left_margin + 10:
                subtype = "list_like"
            elif lw < bw * 0.6 and i == len(b_lines) - 1:
                subtype = "short_line"
                
            line_subtypes[l_idx] = subtype

    
    title_page_like = False
    if len(blocks) < 15 and body_like_blocks <= 2 and title_like_blocks >= 1:
        title_page_like = True
    elif text_density_pct < 5.0 and len(lines) < 30 and title_like_blocks > 0:
        title_page_like = True

    
    suspicious_regions = []
    reasons = []
    
    indent_anomaly_score = 0
    line_height_anomaly_score = 0
    spacing_anomaly_score = 0
    
    lines_sorted = sorted(lines, key=lambda l: l["bbox"][1])
    
    
    for b_idx, b in enumerate(blocks):
        b_type = block_types.get(b_idx, "unknown")
        
        if b_type in ["table", "title"]:
            continue
            
        bx, by, bw, bh = b
        b_lines = [l for l in lines if l["block_idx"] == b_idx]
        b_lines.sort(key=lambda l: l["bbox"][1])
        
        if len(b_lines) < 4:
            continue
            
        
        heights = [l["bbox"][3] for l in b_lines]
        lh_outliers = mad_outliers(heights, threshold=3.5)
        for idx, is_outlier in enumerate(lh_outliers):
            if is_outlier:
                suspicious_regions.append({
                    "bbox": b_lines[idx]["bbox"],
                    "source": "line",
                    "anomaly_type": "line_height",
                    "score": 60 if title_page_like else 80,
                    "area_ratio": (b_lines[idx]["bbox"][2] * b_lines[idx]["bbox"][3]) / (original_w * original_h),
                    "group_type": b_type
                })
                line_height_anomaly_score += 15

        
        lefts = [l["bbox"][0] for l in b_lines]
        
        
        for subtype in ["paragraph_first_line", "continuation_line", "list_like"]:
            subtype_lines = [(i, l) for i, l in enumerate(b_lines) if line_subtypes.get(lines.index(l), "") == subtype and l["bbox"][2] > bw * 0.4]
            if len(subtype_lines) >= 3:
                s_lefts = [l["bbox"][0] for i, l in subtype_lines]
                s_outliers = mad_outliers(s_lefts, threshold=3.5)
                
                for j, is_outlier in enumerate(s_outliers):
                    if is_outlier:
                        idx_in_block = subtype_lines[j][0]
                        line_info = subtype_lines[j][1]
                        
                        
                        local_neighbors = []
                        for n_idx in range(max(0, idx_in_block - 2), min(len(b_lines), idx_in_block + 3)):
                            if n_idx != idx_in_block:
                                local_neighbors.append(b_lines[n_idx]["bbox"][0])
                                
                        if local_neighbors:
                            local_median = np.median(local_neighbors)
                            if abs(line_info["bbox"][0] - local_median) > 10:
                                suspicious_regions.append({
                                    "bbox": line_info["bbox"],
                                    "source": "indent",
                                    "anomaly_type": "left_margin_indent",
                                    "score": 45 if title_page_like else 65,
                                    "area_ratio": (line_info["bbox"][2] * line_info["bbox"][3]) / (original_w * original_h),
                                    "group_type": b_type,
                                    "subtype": subtype
                                })
                                indent_anomaly_score += 15

        
        spacings = []
        for i in range(len(b_lines) - 1):
            l1 = b_lines[i]["bbox"]
            l2 = b_lines[i+1]["bbox"]
            dy = l2[1] - (l1[1] + l1[3])
            spacings.append(dy)
            
            
            if dy < -10 and max(0, min(l1[0]+l1[2], l2[0]+l2[2]) - max(l1[0], l2[0])) > 20:
                suspicious_regions.append({
                    "bbox": [min(l1[0], l2[0]), min(l1[1], l2[1]), max(l1[0]+l1[2], l2[0]+l2[2]) - min(l1[0], l2[0]), max(l1[1]+l1[3], l2[1]+l2[3]) - min(l1[1], l2[1])],
                    "source": "line_spacing",
                    "anomaly_type": "overlapping_lines",
                    "score": 90,
                    "area_ratio": (max(l1[2], l2[2]) * abs(dy)) / (original_w * original_h),
                    "group_type": b_type
                })
                spacing_anomaly_score += 30

        if len(spacings) >= 3:
            sp_outliers = mad_outliers(spacings, threshold=4.0)
            for idx, is_outlier in enumerate(sp_outliers):
                if is_outlier and spacings[idx] > 0:
                    l1 = b_lines[idx]["bbox"]
                    l2 = b_lines[idx+1]["bbox"]
                    suspicious_regions.append({
                        "bbox": [min(l1[0], l2[0]), l1[1]+l1[3], max(l1[2], l2[2]), spacings[idx]],
                        "source": "line_spacing",
                        "anomaly_type": "irregular_spacing",
                        "score": 40 if title_page_like else 65,
                        "area_ratio": (max(l1[2], l2[2]) * spacings[idx]) / (original_w * original_h),
                        "group_type": b_type
                    })
                    spacing_anomaly_score += 15

    
    filtered_regions = []
    suspicious_overlay = img_deskewed.copy()
    
    
    suspicious_regions.sort(key=lambda r: r["score"], reverse=True)
    
    added_bboxes = []
    for r in suspicious_regions:
        x, y, w, h = r["bbox"]
        if r["area_ratio"] < 0.0001: continue
        
        
        overlap_found = False
        for (ex, ey, ew, eh) in added_bboxes:
            if x >= ex and y >= ey and x+w <= ex+ew and y+h <= ey+eh:
                overlap_found = True; break
        if overlap_found: continue
        
        added_bboxes.append((x, y, w, h))
        
        r_out = r.copy()
        r_out["bbox"] = {"x": x, "y": y, "w": w, "h": h}
        r_out["area_ratio"] = round(r["area_ratio"] * 100, 2)
        r_out["score"] = round(r["score"], 1)
        filtered_regions.append(r_out)
        
        cv2.rectangle(suspicious_overlay, (x, y), (x+w, y+h), (0, 0, 255), 2)
        cv2.putText(suspicious_overlay, r["anomaly_type"], (x, max(10, y-5)), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1)

    
    indent_anomaly_score = min(35, indent_anomaly_score)
    line_height_anomaly_score = min(20, line_height_anomaly_score)
    spacing_anomaly_score = min(20, spacing_anomaly_score)
    
    layout_inconsistency_score = indent_anomaly_score + line_height_anomaly_score + spacing_anomaly_score
    
    
    body_regions = [r for r in filtered_regions if r.get("group_type") == "body"]
    anomaly_types_present = set(r["anomaly_type"] for r in body_regions)
    
    suspicion_rule_triggered = ""
    
    if len(filtered_regions) == 0 or (len(body_regions) == 0 and not title_page_like):
        suspicion_level = "НИСКО"
        suspicion_rule_triggered = "Липсват региони или са само извън основния блок"
        if not reasons:
            reasons.append("Оформлението изглежда структурно консистентно. Няма засечени съществени отклонения вътре в блоковете.")
    elif len(body_regions) >= 8 and len(anomaly_types_present) >= 3 and layout_inconsistency_score >= 70:
        suspicion_level = "ВИСОКО"
        suspicion_rule_triggered = "≥ 8 аномални региона и ≥ 3 типа отклонения"
        reasons.append(f"Открити са {len(filtered_regions)} значими и множествени структурни несъответствия (отстъпи, разстояния, височини) в основния текст. Това е силен индикатор за несъответствие в оформлението; потвърждение може да се търси чрез Compare-to-Reference.")
    elif len(body_regions) >= 4 and len(anomaly_types_present) >= 2 and layout_inconsistency_score >= 40:
        suspicion_level = "СРЕДНО"
        suspicion_rule_triggered = "≥ 4 аномални региона и ≥ 2 типа отклонения"
        reasons.append(f"Открити са {len(filtered_regions)} структурни несъответствия в рамките на локалните текстови блокове. Налице са поне два различни вида отклонения.")
    else:
        suspicion_level = "НИСКО"
        suspicion_rule_triggered = "Твърде малко региони или типове за СРЕДНО ниво"
        reasons.append(f"Открити са минимални структурни несъответствия ({list(anomaly_types_present)[0] if anomaly_types_present else 'стилови вариации'}), които най-вероятно са артефакт от сканирането или нормално форматиране.")

    if title_page_like:
        reasons.append("Страницата съдържа заглавна секция или смесен стил (title page); очакваните структурни различия (центрирани редове, отстъпи) са смекчени.")
        suspicion_level = min(suspicion_level, "СРЕДНО")

    
    rel_dir = os.path.join('artifacts', f'job_{job_id}')
    abs_dir = os.path.join(settings.MEDIA_ROOT, rel_dir)
    os.makedirs(abs_dir, exist_ok=True)
    
    paths = {
        "lines": os.path.join(rel_dir, 'layout_lines_overlay.png'),
        "blocks": os.path.join(rel_dir, 'layout_blocks_overlay.png'),
        "suspicious": os.path.join(rel_dir, 'layout_suspicious_overlay.png')
    }
    
    cv2.imwrite(os.path.join(settings.MEDIA_ROOT, paths["lines"]), lines_overlay)
    cv2.imwrite(os.path.join(settings.MEDIA_ROOT, paths["blocks"]), blocks_overlay)
    cv2.imwrite(os.path.join(settings.MEDIA_ROOT, paths["suspicious"]), suspicious_overlay)

    
    largest_region_ratio = max([r["area_ratio"] for r in filtered_regions]) if filtered_regions else 0.0

    metrics = {
        "pipeline": "layout_consistency",
        "pipeline_label": "Консистентност на оформлението",
        "summary": {
            "suspicion_level": suspicion_level,
            "layout_inconsistency_score": layout_inconsistency_score,
            "title_page_like": title_page_like,
            "reasons": reasons
        },
        "subscores": {
            "line_height_anomaly": line_height_anomaly_score,
            "line_spacing_anomaly": spacing_anomaly_score,
            "indent_anomaly": indent_anomaly_score,
            "baseline_tilt_anomaly": 0.0,
            "block_geometry_anomaly": 0.0
        },
        "document_stats": {
            "image_size_px": f"{original_w}x{original_h}",
            "text_density_pct": round(text_density_pct, 2),
            "deskew_angle_deg": round(angle, 2),
            "line_count": len(lines),
            "block_count": len(blocks),
            "title_like_blocks": title_like_blocks,
            "body_like_blocks": body_like_blocks,
            "suspicion_rule_triggered": suspicion_rule_triggered
        },
        "regions_summary": {
            "num_regions": len(filtered_regions),
            "largest_region_area_ratio": round(float(largest_region_ratio)*100, 3)
        },
        "regions": filtered_regions,
        "scores": [
            {"id": "layout_inconsistency_score", "label": "Противоречивост (Score)", "value": layout_inconsistency_score, "desc": "Обща оценка на аномалиите в оформлението (0-100)"},
            {"id": "suspicion_rule_triggered", "label": "Действащо Правило", "value": suspicion_rule_triggered, "desc": "Логическото правило, което е определило краиното ниво на подозрение."},
            {"id": "line_count", "label": "Открити Редове", "value": len(lines), "desc": "Брой открити редове с текст."},
            {"id": "block_count", "label": "Открити Блокове", "value": len(blocks), "desc": "Брой открити параграфи / блокове."},
            {"id": "title_like_blocks", "label": "Заглавни Блокове", "value": title_like_blocks, "desc": "Брой блокове, класифицирани като заглавия."},
            {"id": "body_like_blocks", "label": "Основни Блокове", "value": body_like_blocks, "desc": "Брой блокове, класифицирани като основен текст."}
        ]
    }
    
    overlays_out = [
        {"kind": "overlay", "file": paths["lines"], "label": "Редове (Layout Lines)"},
        {"kind": "overlay", "file": paths["blocks"], "label": "Текстови блокове"},
        {"kind": "overlay", "file": paths["suspicious"], "label": "Подозрителни layout региони"}
    ]
    
    return metrics, overlays_out
