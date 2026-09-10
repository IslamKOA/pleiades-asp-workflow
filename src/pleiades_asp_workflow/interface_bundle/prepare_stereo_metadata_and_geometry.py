#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Prepare metadata, overlap pairs, and stereo geometry for ASP-ready images.

Input folder example:

Stereo:
    A.tif, RPC_A.XML, DIM_A.XML
    B.tif, RPC_B.XML, DIM_B.XML

Tri-stereo:
    A.tif, RPC_A.XML, DIM_A.XML
    B.tif, RPC_B.XML, DIM_B.XML
    C.tif, RPC_C.XML, DIM_C.XML

Custom stereo pairs:
    A_1.tif, RPC_A_1.XML, DIM_A_1.XML
    B_1.tif, RPC_B_1.XML, DIM_B_1.XML
    A_2.tif, RPC_A_2.XML, DIM_A_2.XML
    B_2.tif, RPC_B_2.XML, DIM_B_2.XML

Outputs:
    <out_prefix>_overlap_pairs.csv
    <out_prefix>_image_metadata.csv
    <out_prefix>_stereo_geometry.csv
"""

import os
import argparse
import xml.etree.ElementTree as ET
from itertools import combinations
from math import radians, degrees, sin, cos, acos, tan

import pandas as pd
import rasterio
import xmltodict
from shapely.geometry import box


# ============================================================
# 1. BASIC HELPERS
# ============================================================

def clean_tag(tag):
    """Remove XML namespace if present."""
    return tag.split("}")[-1]


def safe_float(value):
    """Convert value to float safely."""
    if value is None:
        return None

    try:
        return float(str(value).replace(",", ".").strip())
    except Exception:
        return None


def prepared_rpc_path(img_dir, image_id):
    """Prefer RPC_<id>.XML while accepting the legacy <id>.XML name."""
    preferred = os.path.join(img_dir, f"RPC_{image_id}.XML")
    legacy = os.path.join(img_dir, f"{image_id}.XML")
    if os.path.isfile(preferred):
        return preferred
    if os.path.isfile(legacy):
        return legacy
    return preferred


def extract_image_id_from_dim(dim_filename):
    """
    Extract image ID from DIM filename.

    Examples:
    DIM_A.XML     -> A
    DIM_B.XML     -> B
    DIM_A_1.XML   -> A_1
    """

    name = os.path.basename(dim_filename)
    name = os.path.splitext(name)[0]

    if name.startswith("DIM_"):
        return name.replace("DIM_", "", 1)

    return name


# ============================================================
# 2. FIND PREPARED FILES
# ============================================================

def find_image_ids(img_dir):
    """
    Find prepared image IDs from .tif files that have matching .XML files.
    """

    image_ids = []

    for filename in sorted(os.listdir(img_dir)):
        if filename.lower().endswith(".tif"):
            image_id = os.path.splitext(filename)[0]

            tif_path = os.path.join(img_dir, f"{image_id}.tif")
            rpc_path = prepared_rpc_path(img_dir, image_id)

            if os.path.isfile(tif_path) and os.path.isfile(rpc_path):
                image_ids.append(image_id)

    return image_ids


def parse_pairs_argument(pairs_arg):
    """
    Parse optional pair list.

    Example:
    --pairs A_1:B_1 A_2:B_2
    """

    if not pairs_arg:
        return None

    pairs = []

    for item in pairs_arg:
        if ":" not in item:
            raise ValueError(
                f"Invalid pair format: {item}. Use LEFT:RIGHT, for example A:B."
            )

        left, right = item.split(":", 1)
        pairs.append((left.strip(), right.strip()))

    return pairs


# ============================================================
# 3. OVERLAP FROM RPC FOOTPRINT
# ============================================================

def get_rpc_params(xml_path):
    """Read basic RPC normalization parameters from RPC XML."""

    with open(xml_path, "r") as f:
        data = xmltodict.parse(f.read())

    rpc = data.get("RPC", {})

    return {
        "SAMP_OFF": float(rpc.get("SAMP_OFF", 0)),
        "SAMP_SCALE": float(rpc.get("SAMP_SCALE", 1)),
        "LINE_OFF": float(rpc.get("LINE_OFF", 0)),
        "LINE_SCALE": float(rpc.get("LINE_SCALE", 1)),
        "LAT_OFF": float(rpc.get("LAT_OFF", 0)),
        "LAT_SCALE": float(rpc.get("LAT_SCALE", 1)),
        "LONG_OFF": float(rpc.get("LONG_OFF", 0)),
        "LONG_SCALE": float(rpc.get("LONG_SCALE", 1)),
    }


def approximate_footprint(img_path, rpc):
    """
    Approximate image footprint from RPC offsets/scales.

    This is only used as a practical overlap check before ASP processing.
    """

    with rasterio.open(img_path) as src:
        width, height = src.width, src.height

    x_min = -rpc["SAMP_OFF"] / rpc["SAMP_SCALE"]
    x_max = (width - rpc["SAMP_OFF"]) / rpc["SAMP_SCALE"]

    y_min = -rpc["LINE_OFF"] / rpc["LINE_SCALE"]
    y_max = (height - rpc["LINE_OFF"]) / rpc["LINE_SCALE"]

    lon_min = rpc["LONG_OFF"] + x_min * rpc["LONG_SCALE"]
    lon_max = rpc["LONG_OFF"] + x_max * rpc["LONG_SCALE"]

    lat_min = rpc["LAT_OFF"] + y_min * rpc["LAT_SCALE"]
    lat_max = rpc["LAT_OFF"] + y_max * rpc["LAT_SCALE"]

    return box(lon_min, lat_min, lon_max, lat_max)


def build_footprints(img_dir, image_ids):
    """Build approximate footprint for each image."""

    footprints = {}

    for image_id in image_ids:
        img_path = os.path.join(img_dir, f"{image_id}.tif")
        rpc_path = prepared_rpc_path(img_dir, image_id)

        try:
            rpc = get_rpc_params(rpc_path)
            footprints[image_id] = approximate_footprint(img_path, rpc)

        except Exception as e:
            print(f"WARNING: Could not build footprint for {image_id}: {e}")

    return footprints


def compute_overlap_table(footprints, pairs, iou_thresh):
    """Compute overlap table for selected pairs."""

    records = []

    for left, right in pairs:
        if left not in footprints or right not in footprints:
            records.append({
                "pair": f"{left}{right}",
                "left_image": left,
                "right_image": right,
                "iou": None,
                "is_valid": False,
                "note": "missing footprint",
            })
            continue

        poly_left = footprints[left]
        poly_right = footprints[right]

        inter_area = poly_left.intersection(poly_right).area
        union_area = poly_left.union(poly_right).area

        iou = inter_area / union_area if union_area > 0 else 0.0
        is_valid = iou >= iou_thresh

        records.append({
            "pair": f"{left}{right}",
            "left_image": left,
            "right_image": right,
            "iou": round(iou, 4),
            "is_valid": is_valid,
            "note": "",
        })

    return pd.DataFrame(records)


# ============================================================
# 4. DIM METADATA
# ============================================================

def parse_dim_xml(xml_path):
    """Parse DIM XML safely."""

    try:
        tree = ET.parse(xml_path)
        return tree.getroot()
    except ET.ParseError as e:
        print(f"WARNING: Could not parse {xml_path}: {e}")
        return None


def extract_dim_metadata(root, xml_path):
    """Extract useful image metadata from DIM XML."""

    image_id = extract_image_id_from_dim(xml_path)

    metadata = {
        "image_id": image_id,
        "dim_file": os.path.basename(xml_path),
        "tif_file": f"{image_id}.tif",
        "rpc_file": f"RPC_{image_id}.XML",
    }

    target_tags = {
        "NBANDS",
        "NBITS",
        "FOCAL_LENGTH",
        "AZIMUTH_ANGLE",
        "VIEWING_ANGLE_ACROSS_TRACK",
        "VIEWING_ANGLE_ALONG_TRACK",
        "VIEWING_ANGLE",
        "INCIDENCE_ANGLE_ALONG_TRACK",
        "INCIDENCE_ANGLE_ACROSS_TRACK",
        "INCIDENCE_ANGLE",
        "SUN_AZIMUTH",
        "SUN_ELEVATION",
        "IMAGING_DATE",
        "IMAGING_TIME",
    }

    for elem in root.iter():
        tag = clean_tag(elem.tag)
        text = elem.text.strip() if elem.text and elem.text.strip() else None

        if tag in target_tags:
            metadata[tag] = text

        if tag == "Planimetric_Accuracy":
            for sub_elem in elem:
                sub_tag = clean_tag(sub_elem.tag)
                sub_text = sub_elem.text.strip() if sub_elem.text and sub_elem.text.strip() else None

                if sub_tag in ["MEAN", "STDV", "CE90"]:
                    metadata[f"Planimetric_Accuracy_{sub_tag}"] = sub_text

    return metadata


def build_image_metadata_table(img_dir, image_ids):
    """Build one-row-per-image metadata table."""

    records = []

    for image_id in image_ids:
        dim_path = os.path.join(img_dir, f"DIM_{image_id}.XML")

        if not os.path.isfile(dim_path):
            print(f"WARNING: DIM file missing for {image_id}: {dim_path}")
            continue

        root = parse_dim_xml(dim_path)

        if root is None:
            continue

        records.append(
            extract_dim_metadata(root, dim_path)
        )

    metadata_df = pd.DataFrame(records)

    if metadata_df.empty:
        return metadata_df

    if "IMAGING_DATE" in metadata_df.columns and "IMAGING_TIME" in metadata_df.columns:
        metadata_df = metadata_df.sort_values(
            by=["IMAGING_DATE", "IMAGING_TIME"]
        ).reset_index(drop=True)

    if len(metadata_df) == 3:
        metadata_df.insert(1, "view_order", ["Forward", "Middle", "Backward"])
    elif len(metadata_df) == 2:
        metadata_df.insert(1, "view_order", ["Image_1", "Image_2"])
    else:
        metadata_df.insert(
            1,
            "view_order",
            [f"Image_{i+1}" for i in range(len(metadata_df))]
        )

    return metadata_df


# ============================================================
# 5. STEREO GEOMETRY: B/H AND STEREO ANGLE
# ============================================================

class ImageGeometry:
    """
    Store image viewing geometry.

    B/H computation uses:
    - INCIDENCE_ANGLE_ALONG_TRACK
    - INCIDENCE_ANGLE_ACROSS_TRACK
    - AZIMUTH_ANGLE
    """

    def __init__(self, image_id, along, across, azimuth):
        self.image_id = image_id

        self.scan = radians(float(along))
        self.ortho = radians(float(across))
        self.azimuth = radians(float(azimuth))

        # Same sign convention as your original script
        self.ortho = -self.ortho
        self.azimuth = -self.azimuth

        self.compute_components()

    def compute_components(self):
        self.s_comp = cos(self.ortho) * sin(self.scan)
        self.o_comp = cos(self.scan) * sin(self.ortho)
        self.z_comp = cos(self.ortho) * cos(self.scan)


def compute_bh_and_stereo_angle(im1, im2):
    """
    Compute stereo angle and approximate B/H ratio.

    Formula:
        B/H = 2 * tan(stereo_angle / 2)
    """

    delta_az = im2.azimuth - im1.azimuth

    rot_z = [
        [cos(delta_az), -sin(delta_az), 0],
        [sin(delta_az),  cos(delta_az), 0],
        [0,              0,             1],
    ]

    p1 = [im1.s_comp, im1.o_comp, im1.z_comp]

    p1_rot = [
        sum(a * b for a, b in zip(row, p1))
        for row in rot_z
    ]

    p2 = [im2.s_comp, im2.o_comp, im2.z_comp]

    dot = sum(a * b for a, b in zip(p1_rot, p2))

    # Numerical safety
    dot = max(min(dot, 1.0), -1.0)

    stereo_angle_rad = acos(dot)
    stereo_angle_deg = degrees(stereo_angle_rad)

    bh = 2.0 * tan(stereo_angle_rad / 2.0)

    return stereo_angle_deg, bh


def build_stereo_geometry_table(metadata_df, pairs):
    """Build pair-level B/H and stereo-angle table."""

    required_cols = [
        "image_id",
        "INCIDENCE_ANGLE_ALONG_TRACK",
        "INCIDENCE_ANGLE_ACROSS_TRACK",
        "AZIMUTH_ANGLE",
    ]

    missing_cols = [c for c in required_cols if c not in metadata_df.columns]

    if missing_cols:
        print("WARNING: Cannot compute stereo geometry. Missing columns:")
        print(missing_cols)
        return pd.DataFrame(columns=[
            "pair",
            "left_image",
            "right_image",
            "stereo_angle_deg",
            "B_over_H",
            "geometry_source",
        ])

    metadata_index = metadata_df.set_index("image_id")

    records = []

    for left, right in pairs:
        if left not in metadata_index.index or right not in metadata_index.index:
            records.append({
                "pair": f"{left}{right}",
                "left_image": left,
                "right_image": right,
                "stereo_angle_deg": None,
                "B_over_H": None,
                "geometry_source": "missing metadata",
            })
            continue

        row_left = metadata_index.loc[left]
        row_right = metadata_index.loc[right]

        values = [
            row_left["INCIDENCE_ANGLE_ALONG_TRACK"],
            row_left["INCIDENCE_ANGLE_ACROSS_TRACK"],
            row_left["AZIMUTH_ANGLE"],
            row_right["INCIDENCE_ANGLE_ALONG_TRACK"],
            row_right["INCIDENCE_ANGLE_ACROSS_TRACK"],
            row_right["AZIMUTH_ANGLE"],
        ]

        if any(safe_float(v) is None for v in values):
            records.append({
                "pair": f"{left}{right}",
                "left_image": left,
                "right_image": right,
                "stereo_angle_deg": None,
                "B_over_H": None,
                "geometry_source": "missing angle value",
            })
            continue

        im1 = ImageGeometry(
            image_id=left,
            along=row_left["INCIDENCE_ANGLE_ALONG_TRACK"],
            across=row_left["INCIDENCE_ANGLE_ACROSS_TRACK"],
            azimuth=row_left["AZIMUTH_ANGLE"],
        )

        im2 = ImageGeometry(
            image_id=right,
            along=row_right["INCIDENCE_ANGLE_ALONG_TRACK"],
            across=row_right["INCIDENCE_ANGLE_ACROSS_TRACK"],
            azimuth=row_right["AZIMUTH_ANGLE"],
        )

        stereo_angle_deg, bh = compute_bh_and_stereo_angle(im1, im2)

        records.append({
            "pair": f"{left}{right}",
            "left_image": left,
            "right_image": right,
            "stereo_angle_deg": round(stereo_angle_deg, 3),
            "B_over_H": round(bh, 3),
            "geometry_source": "incidence_angles_and_azimuth",
        })

    return pd.DataFrame(records)


# ============================================================
# 6. MAIN FUNCTION
# ============================================================

def main(img_dir, out_prefix, iou_thresh=0.05, pairs_arg=None):
    img_dir = os.path.abspath(img_dir)
    out_prefix = os.path.abspath(out_prefix)

    if not os.path.isdir(img_dir):
        raise FileNotFoundError(f"Image directory not found: {img_dir}")

    image_ids = find_image_ids(img_dir)

    if len(image_ids) < 2:
        raise RuntimeError(
            f"At least two image/RPC pairs are required. Found: {image_ids}"
        )

    user_pairs = parse_pairs_argument(pairs_arg)

    if user_pairs is None:
        pairs = list(combinations(image_ids, 2))
    else:
        pairs = user_pairs

    print("\nDetected prepared image IDs:")
    print(image_ids)

    print("\nStereo pairs to evaluate:")
    for left, right in pairs:
        print(f"  - {left} / {right}")

    # --------------------------------------------------------
    # Overlap table
    # --------------------------------------------------------

    footprints = build_footprints(img_dir, image_ids)

    overlap_df = compute_overlap_table(
        footprints=footprints,
        pairs=pairs,
        iou_thresh=iou_thresh
    )

    # --------------------------------------------------------
    # Image metadata table
    # --------------------------------------------------------

    metadata_df = build_image_metadata_table(
        img_dir=img_dir,
        image_ids=image_ids
    )

    # --------------------------------------------------------
    # Stereo geometry table
    # --------------------------------------------------------

    geometry_df = build_stereo_geometry_table(
        metadata_df=metadata_df,
        pairs=pairs
    )

    # --------------------------------------------------------
    # Save outputs
    # --------------------------------------------------------

    overlap_csv = f"{out_prefix}_overlap_pairs.csv"
    metadata_csv = f"{out_prefix}_image_metadata.csv"
    geometry_csv = f"{out_prefix}_stereo_geometry.csv"

    overlap_df.to_csv(overlap_csv, index=False)
    metadata_df.to_csv(metadata_csv, index=False)
    geometry_df.to_csv(geometry_csv, index=False)

    print("\nSaved:")
    print(overlap_csv)
    print(metadata_csv)
    print(geometry_csv)


# ============================================================
# 7. COMMAND-LINE INTERFACE
# ============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate overlap, image metadata, and stereo geometry tables."
    )

    parser.add_argument(
        "-img_dir",
        type=str,
        required=True,
        help="Directory containing prepared .tif, .XML, and DIM_*.XML files."
    )

    parser.add_argument(
        "-out_prefix",
        type=str,
        required=True,
        help="Output prefix for generated CSV files."
    )

    parser.add_argument(
        "-thresh",
        type=float,
        default=0.05,
        help="Minimum IoU threshold for valid overlap."
    )

    parser.add_argument(
        "--pairs",
        nargs="*",
        default=None,
        help="Optional pair list, for example: --pairs A:B A:C B:C or A_1:B_1 A_2:B_2"
    )

    args = parser.parse_args()

    main(
        img_dir=args.img_dir,
        out_prefix=args.out_prefix,
        iou_thresh=args.thresh,
        pairs_arg=args.pairs
    )