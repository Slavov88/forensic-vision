import os
import cv2
import numpy as np
from django.conf import settings

def run_general_scan(image_path, job_id, params=None):
    """
    Forensics-grade general scan: Background-aware analysis (Noise/Edges),
    BBox filtering (Aspect Ratio & Text Overlap), and explainable results.
    """
    params = params or {}
    img = cv2.imread(image_path)
    if img is None:
        raise ValueError(f"Could not read image at {image_path}")

    
    roi = params.get('roi')
    original_h, original_w = img.shape[:2]
    base_overlay = img.copy()
    
    if roi:
        try:
            rx, ry, rw, rh = roi['x'], roi['y'], roi['w'], roi['h']
            rx, ry = max(0, rx), max(0, ry)
            rw, rh = min(original_w - rx, rw), min(original_h - ry, rh)
            cv2.rectangle(base_overlay, (rx, ry), (rx+rw, ry+rh), (255, 255, 0), 3)
            img = img[ry:ry+rh, rx:rx+rw]
        except Exception:
            pass 

    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    
    fg_mask = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 15, 8)
    
    kernel = np.ones((3, 3), np.uint8)
    fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, kernel)
    fg_mask = cv2.dilate(fg_mask, kernel, iterations=1)
    bg_mask = cv2.bitwise_not(fg_mask)

    
    n_block = 16
    noise_map = np.zeros((h // n_block, w // n_block), dtype=np.float32)
    edge_map = np.zeros_like(noise_map)
    block_bg_mask = np.zeros_like(noise_map, dtype=np.uint8)
    
    
    sobel_x = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    sobel_y = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    sobel_mag = np.sqrt(sobel_x**2 + sobel_y**2)

    for i in range(0, h - n_block, n_block):
        for j in range(0, w - n_block, n_block):
            bi, bj = i // n_block, j // n_block
            if bi >= noise_map.shape[0] or bj >= noise_map.shape[1]: continue
            
            block_gray = gray[i:i+n_block, j:j+n_block]
            block_bg = bg_mask[i:i+n_block, j:j+n_block]
            block_edges = sobel_mag[i:i+n_block, j:j+n_block]
            
            bg_pixels = block_bg > 0
            if np.sum(bg_pixels) > (n_block * n_block * 0.15): # At least 15% background
                block_bg_mask[bi, bj] = 1
                
                noise_map[bi, bj] = np.var(block_gray[bg_pixels])
                
                edge_map[bi, bj] = np.mean(block_edges[bg_pixels])

    # Statistics must include ALL background blocks (even zero-noise ones) to identify outliers
    bg_has_blocks = np.any(block_bg_mask > 0)
    noise_values = noise_map[block_bg_mask > 0] if bg_has_blocks else np.array([0])
    noise_mean = np.mean(noise_values) if bg_has_blocks else 1.0
    noise_std = np.std(noise_values) if bg_has_blocks else 0.0
    noise_inconsistency_score = (noise_std / noise_mean) if noise_mean > 0.0 else 0.0
    
    edge_values = edge_map[block_bg_mask > 0] if bg_has_blocks else np.array([0])
    edge_mean = np.mean(edge_values) if bg_has_blocks else 1.0
    edge_std = np.std(edge_values) if bg_has_blocks else 0.0
    edge_anomaly_score = (edge_std / edge_mean) if edge_mean > 0.0 else 0.0

    
    regions = []
    text_density = np.sum(fg_mask > 0) / (h * w)
    is_text_page = text_density > 0.03 # 3% text density

    # Arrays to store hotspot counts
    noise_hotspots = 0
    edge_hotspots = 0
    total_bg_blocks = np.sum(block_bg_mask > 0)

    def extract_regions(m, mean, std, source):
        nonlocal noise_hotspots, edge_hotspots
        # Increased threshold to 3.5 Z-Score for robustness
        thresh_val = mean + 3.5 * std
        # Mask only actual background blocks
        is_above_thresh = (m > thresh_val) & (block_bg_mask > 0)
        thresholded = is_above_thresh.astype(np.uint8) * 255
        
        if source == "noise":
            noise_hotspots += np.sum(is_above_thresh)
        else:
            edge_hotspots += np.sum(is_above_thresh)
            
        cnts, _ = cv2.findContours(thresholded, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        found = []
        for c in cnts:
            area = cv2.contourArea(c)
            if area < 1: continue
            
            bx, by, bw, bh = cv2.boundingRect(c)
            ix, iy, iw, ih = bx*n_block, by*n_block, bw*n_block, bh*n_block
            
            
            aspect = iw / ih if ih > 0 else 0
            if is_text_page and aspect > 8 and ih < (2 * n_block):
                continue
            
            
            roi_fg = fg_mask[iy:iy+ih, ix:ix+iw]
            overlap_ratio = np.sum(roi_fg > 0) / (iw * ih)
            if overlap_ratio > 0.35:
                continue

            gx, gy = ix, iy
            if roi: gx += roi['x']; gy += roi['y']
            
            score_val = np.max(m[by:by+bh, bx:bx+bw]) / mean if mean > 0 else 0
            found.append({
                "bbox": {"x": gx, "y": gy, "w": iw, "h": ih},
                "area_ratio": round(area / (m.shape[0] * m.shape[1]) * 100, 2),
                "score": round(float(score_val), 2),
                "source": source
            })
        return found

    regions += extract_regions(noise_map, noise_mean, noise_std, "noise")
    regions += extract_regions(edge_map, edge_mean, edge_std, "edges")
    regions = sorted(regions, key=lambda x: x["score"], reverse=True)[:10]

    num_regions = len(regions)
    largest_region_area_ratio = max([r["area_ratio"] for r in regions]) if num_regions > 0 else 0.0
    noise_hotspot_ratio = (noise_hotspots / total_bg_blocks * 100) if total_bg_blocks > 0 else 0.0
    edge_hotspot_ratio = (edge_hotspots / total_bg_blocks * 100) if total_bg_blocks > 0 else 0.0

    
    noise_norm = cv2.normalize(noise_map, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    noise_heatmap = cv2.resize(cv2.applyColorMap(noise_norm, cv2.COLORMAP_JET), (w, h), interpolation=cv2.INTER_NEAREST)
    edge_norm = cv2.normalize(edge_map, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    edge_heatmap = cv2.resize(cv2.applyColorMap(edge_norm, cv2.COLORMAP_MAGMA), (w, h), interpolation=cv2.INTER_NEAREST)

    combined_overlay = base_overlay.copy()
    for r in regions:
        b = r["bbox"]
        color = (0, 0, 255) if r["source"] == "noise" else (255, 0, 0)
        cv2.rectangle(combined_overlay, (b["x"], b["y"]), (b["x"]+b["w"], b["y"]+b["h"]), color, 2)

    
    rel_dir = os.path.join('artifacts', f'job_{job_id}')
    abs_dir = os.path.join(settings.MEDIA_ROOT, rel_dir)
    os.makedirs(abs_dir, exist_ok=True)
    
    paths = {
        "noise": os.path.join(rel_dir, 'noise_bg_heatmap.png'),
        "edges": os.path.join(rel_dir, 'edges_bg_heatmap.png'),
        "combined": os.path.join(rel_dir, 'background_anomalies.png')
    }
    cv2.imwrite(os.path.join(settings.MEDIA_ROOT, paths["noise"]), noise_heatmap)
    cv2.imwrite(os.path.join(settings.MEDIA_ROOT, paths["edges"]), edge_heatmap)
    cv2.imwrite(os.path.join(settings.MEDIA_ROOT, paths["combined"]), combined_overlay)

    
    reasons = []
    
    
    if is_text_page:
        reasons.append(f"Страницата е текстова ({text_density*100:.1f}% текст); контурните метрики са на заден план.")
    
    
    if num_regions == 0:
        reasons.append("Не са открити локализирани подозрителни региони (bbox).")
        
        score_total = (noise_inconsistency_score * 35) + (edge_anomaly_score * (15 if is_text_page else 35))
        if score_total > 50:
             suspicion_level = "Ниско"
             reasons.append("Отчетени са леки глобални фонови вариации, но липсват локализирани аномалии.")
        else:
             suspicion_level = "Няма"
             reasons.append("Фоновият шум и контурите са напълно консистентни.")
    else:
        reasons.append(f"Открити са {num_regions} региона с нетипичен фон (макс площ {largest_region_area_ratio}%).")
        if noise_hotspot_ratio > 5.0 or noise_inconsistency_score > 1.8:
            reasons.append("Забелязана силна аномалия в шумовия фон (bg noise).")
        if not is_text_page and (edge_hotspot_ratio > 5.0 or edge_anomaly_score > 1.2):
             reasons.append("Аномална контурна енергия в зоните без текст.")
             
        score_total = (noise_inconsistency_score * 35) + (edge_anomaly_score * (15 if is_text_page else 35)) + (num_regions * 10) + (largest_region_area_ratio * 2)
        if score_total > 110: suspicion_level = "Високо"
        elif score_total > 70: suspicion_level = "Средно"
        else: suspicion_level = "Ниско"

    
    blur_score = cv2.Laplacian(gray, cv2.CV_64F).var()
    blur_label = "Ниска" if blur_score < 100 else ("Средна" if blur_score < 500 else "Висока")

    metrics = {
        "summary": {
            "quality_level": "Лошо" if blur_score < 100 else ("Добро" if blur_score < 500 else "Отлично"),
            "suspicion_level": suspicion_level,
            "reasons": reasons,
            "thresholds_used": {"noise_z_score": 3.5, "edge_z_score": 3.5, "text_mask": "Adaptive+Morph", "min_bg_ratio": 0.15}
        },
        "scores": [
            {"id": "num_regions", "label": "Брой Аномалии", "value": num_regions, "desc": "Количество локализирани подозрителни зони."},
            {"id": "largest_region_area_ratio", "label": "Макс площ аномалия (%)", "value": round(float(largest_region_area_ratio), 2), "desc": "Площ на най-големия засечен регион спрямо общия фон."},
            {"id": "noise_hotspot_ratio", "label": "Hotspots Шум (%)", "value": round(float(noise_hotspot_ratio), 2), "desc": "Процент от фона с нетипичен шум."},
            {"id": "edge_hotspot_ratio", "label": "Hotspots Контури (%)", "value": round(float(edge_hotspot_ratio), 2), "desc": "Процент от фона с нетипични контури."},
            {"id": "noise_inconsistency_score", "label": "Аномалия във фона (Noise)", "value": round(float(noise_inconsistency_score), 3), "desc": "Вариация на шума само в празните зони на документа."},
            {"id": "edge_anomaly_score", "label": "Контурна аномалия (Edges)", "value": round(float(edge_anomaly_score), 3), "desc": "Енергия на контурите, филтрирана от текстовото съдържание."},
            {"id": "text_density_pct", "label": "Плътност на текста", "value": round(float(text_density * 100), 2), "desc": "Процент съдържание спрямо общата площ."},
            {"id": "blur_score", "label": "Рязкост (Score)", "value": round(float(blur_score), 1), "desc": "Оценка на фокуса и детайлите."},
            {"id": "blur_level", "label": "Ниво на рязкост", "value": blur_label, "desc": "Качествено ниво на рязкост."},
            {"id": "image_size_px", "label": "Размер (px)", "value": f"{w}x{h}", "desc": "Ширина и височина на анализирания обект."},
            {"id": "dpi_assumed", "label": "DPI (базово)", "value": 150, "desc": "Предполагаема резолюция за изчисление на мащаб."}
        ],
        "regions": regions,
        "target": {"size": f"{w}x{h}", "roi": roi, "text_mode": is_text_page}
    }
    overlays = [
        {"kind": "overlay", "file": paths["noise"], "label": "Шумова карта (само фон)"},
        {"kind": "overlay", "file": paths["edges"], "label": "Контурна карта (само фон)"},
        {"kind": "overlay", "file": paths["combined"], "label": "Зони с нетипичен фон"}
    ]
    return metrics, overlays
