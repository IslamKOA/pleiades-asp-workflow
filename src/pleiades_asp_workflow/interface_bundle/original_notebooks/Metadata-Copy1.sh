#!/usr/bin/env bash
set -euo pipefail


# ============================================================
# 1. USER SETTINGS
# ============================================================
#
# Only this section normally needs to be edited.
# ============================================================

# ------------------------------------------------------------
# 1.1 Acquisition settings
# ------------------------------------------------------------

BASE_DIR="/mnt/summer/USERS/KOAI/Berared_Aug24"
area_name="Berared_Aug24"

# pleiades | pleiades_neo | spot
sensor_type="pleiades"

# Images available:
#   stereo     -> A and B
#   tri_stereo -> A, B, and C
acquisition_mode="tri_stereo"

# true -> cropped images; false -> full prepared images
use_cropped_images=true

# true -> replace existing outputs
OVERWRITE_OUTPUTS=true


# ------------------------------------------------------------
# 1.2 Reference topography and output grid
# ------------------------------------------------------------

ALIGNMENT_DEM_RELATIVE="LiDAR_DEMs/Ellps/Berared_LiDAR_DSM1m_WGS84_31N.tif"
MAPPROJECT_DEM_RELATIVE="LiDAR_DEMs/Ellps/Berared_LiDAR_DSM50m_WGS84_31N.tif"

TARGET_EPSG="32632"
rawRes_meters=0.5


# ------------------------------------------------------------
# 1.3 Preliminary stereo configuration
# ------------------------------------------------------------
#
# Used only for the preliminary DSM required by pc_align.
# Keep this separate from the final stereo settings.
# ------------------------------------------------------------

PRELIM_STEREO_ALGORITHM="asp_bm"
PRELIM_XCORR_THRESHOLD=2
PRELIM_COST_MODE=2
PRELIM_CORR_KERNEL=(35 35)
PRELIM_SUBPIXEL_KERNEL=(45 45)
PRELIM_SUBPIXEL_MODE=2


# ------------------------------------------------------------
# 1.4 Final stereo algorithm and kernel settings
# ------------------------------------------------------------

corr_mem_limit_mb=10240
corr_tile_size=3200
spr=2
FINAL_XCORR_THRESHOLD=2

# asp_bm | asp_sgm | asp_mgm
FINAL_STEREO_ALGORITHM="asp_mgm"

# Kernel values are paired by array index.
bmCKernels=(9)
bmSKernels=(21)
bmCM=2

mgmCKernels=(9)
mgmSKernels=(21)
mgmCM=4

sgmCKernels=(9)
sgmSKernels=(21)
sgmCM=4


# ------------------------------------------------------------
# 1.5 Final stereo product mode
# ------------------------------------------------------------
#
# Select exactly one:
#
#   single
#       Run one or more independent two-image pairs.
#
#   dual
#       Run one or more ordered three-image configurations,
#       for example "A B C", "B A C", or "C A B".
#
#   tri
#       Automatically run A-B, A-C, and B-C, then merge the
#       three point clouds in the later tri-stereo merge step.
# ------------------------------------------------------------

FINAL_STEREO_MODE="dual"


# Used only when FINAL_STEREO_MODE="single".
# One pair:
#   FINAL_SINGLE_PAIRS=("A B")
# Several pairs:
#   FINAL_SINGLE_PAIRS=("A B" "A C" "B C")
FINAL_SINGLE_PAIRS=(
    "A B"
)


# Used only when FINAL_STEREO_MODE="dual".
# One configuration:
#   FINAL_DUAL_CONFIGURATIONS=("A B C")
# Several configurations:
#   FINAL_DUAL_CONFIGURATIONS=("A B C" "B A C" "C A B")
FINAL_DUAL_CONFIGURATIONS=(
    "C A B"
)


# ------------------------------------------------------------
# 1.6 Final point-cloud and DSM settings
# ------------------------------------------------------------
#
# Final point clouds and final DSMs are stored in separate
# folders under ASP_OUTPUT_DIR.
# ------------------------------------------------------------

FINAL_POINT_CLOUD_FOLDER_NAME="point_clouds"
FINAL_DSM_FOLDER_NAME="final_dsms"

# Final DSM pixel size in metres
FINAL_DSM_RESOLUTION=1.0

# Maximum accepted triangulation error in metres
FINAL_MAX_VALID_TRIANGULATION_ERROR=1.0

# Final DSM no-data value
FINAL_DSM_NODATA=-9999

# point2dem threads:
#   0 -> use the ASP/Vision Workbench default
FINAL_DSM_THREADS=0

# true -> also create the triangulation-error raster
FINAL_DSM_CREATE_ERROR_IMAGE=true

# GeoTIFF compression used by point2dem
FINAL_DSM_COMPRESSION="LZW"

# Threads used by pc_merge in tri-stereo mode
FINAL_PC_MERGE_THREADS=4

# Backward-compatible alias used by older notebook cells
resOut_meters="${FINAL_DSM_RESOLUTION}"


# ============================================================
# 2. WORKING ENVIRONMENT
# ============================================================

if [[ ! -d "${BASE_DIR}" ]]; then
    echo "ERROR: Base directory does not exist:" >&2
    echo "  ${BASE_DIR}" >&2
    return 1 2>/dev/null || exit 1
fi

cd "${BASE_DIR}" || {
    echo "ERROR: Cannot enter the base directory:" >&2
    echo "  ${BASE_DIR}" >&2
    return 1 2>/dev/null || exit 1
}

# Prevent conflicts between ASP and external libraries
export GDAL_DRIVER_PATH=disable
unset LD_LIBRARY_PATH
unset PYTHONPATH


# ============================================================
# 3. AUTOMATIC SENSOR SETTINGS
# ============================================================

case "${sensor_type}" in
    pleiades|pleiades_neo|spot)
        CAMERA_SESSION="rpc"
        ;;
    *)
        echo "ERROR: Unsupported sensor_type: ${sensor_type}" >&2
        return 1 2>/dev/null || exit 1
        ;;
esac


# ============================================================
# 4. AUTOMATIC INPUT IMAGE SELECTION
# ============================================================

PREPARED_DIR="${BASE_DIR}/merged_tiles"

case "${use_cropped_images}" in
    true)
        INPUT_DIR="${PREPARED_DIR}/cropped_images"
        FILE_SUFFIX="_crop"
        INPUT_TYPE="cropped"
        ;;
    false)
        INPUT_DIR="${PREPARED_DIR}"
        FILE_SUFFIX=""
        INPUT_TYPE="full"
        ;;
    *)
        echo "ERROR: use_cropped_images must be true or false." >&2
        return 1 2>/dev/null || exit 1
        ;;
esac

case "${acquisition_mode}" in
    stereo)
        VIEW_IDS=(A B)
        ;;
    tri_stereo)
        VIEW_IDS=(A B C)
        ;;
    *)
        echo "ERROR: acquisition_mode must be stereo or tri_stereo." >&2
        return 1 2>/dev/null || exit 1
        ;;
esac

IMAGE_FILES=()
RPC_FILES=()
DIM_FILES=()

for view in "${VIEW_IDS[@]}"; do
    IMAGE_FILES+=("${INPUT_DIR}/${view}${FILE_SUFFIX}.tif")
    RPC_FILES+=("${INPUT_DIR}/${view}${FILE_SUFFIX}.XML")
    DIM_FILES+=("${INPUT_DIR}/DIM_${view}${FILE_SUFFIX}.XML")
done

# stereo -> AB; tri-stereo -> ABC
VIEW_TAG=$(printf "%s" "${VIEW_IDS[@]}")

# ============================================================
# 5A. AUTOMATIC FINAL-STEREO CONFIGURATION
# ============================================================

# Select active algorithm parameters.
case "${FINAL_STEREO_ALGORITHM}" in
    asp_bm)
        FINAL_ALGORITHM_TAG="BM"
        FINAL_CKERNELS=("${bmCKernels[@]}")
        FINAL_SKERNELS=("${bmSKernels[@]}")
        FINAL_COST_MODE="${bmCM}"
        ;;

    asp_sgm)
        FINAL_ALGORITHM_TAG="SGM"
        FINAL_CKERNELS=("${sgmCKernels[@]}")
        FINAL_SKERNELS=("${sgmSKernels[@]}")
        FINAL_COST_MODE="${sgmCM}"
        ;;

    asp_mgm)
        FINAL_ALGORITHM_TAG="MGM"
        FINAL_CKERNELS=("${mgmCKernels[@]}")
        FINAL_SKERNELS=("${mgmSKernels[@]}")
        FINAL_COST_MODE="${mgmCM}"
        ;;

    *)
        echo "ERROR: Unsupported FINAL_STEREO_ALGORITHM:" >&2
        echo "  ${FINAL_STEREO_ALGORITHM}" >&2
        echo "Allowed values: asp_bm, asp_sgm, asp_mgm" >&2
        return 1 2>/dev/null || exit 1
        ;;
esac


if (( ${#FINAL_CKERNELS[@]} == 0 )); then
    echo "ERROR: No final correlation kernels are defined." >&2
    return 1 2>/dev/null || exit 1
fi

if (( ${#FINAL_CKERNELS[@]} != ${#FINAL_SKERNELS[@]} )); then
    echo "ERROR: Final correlation and subpixel kernel arrays" >&2
    echo "must contain the same number of values." >&2
    echo "Correlation kernels: ${FINAL_CKERNELS[*]}" >&2
    echo "Subpixel kernels   : ${FINAL_SKERNELS[*]}" >&2
    return 1 2>/dev/null || exit 1
fi


view_is_available() {
    local requested_view="$1"
    local available_view

    for available_view in "${VIEW_IDS[@]}"; do
        [[ "${requested_view}" == "${available_view}" ]] && return 0
    done

    return 1
}


ACTIVE_SINGLE_PAIRS=()
ACTIVE_SINGLE_PAIR_TAGS=()

ACTIVE_DUAL_CONFIGURATIONS=()
ACTIVE_DUAL_CONFIGURATION_TAGS=()

TRI_MERGE_REQUIRED=false
TRI_MERGE_PAIR_TAGS=()
TRI_MERGE_TAG=""


case "${FINAL_STEREO_MODE}" in
    single)
        if (( ${#FINAL_SINGLE_PAIRS[@]} == 0 )); then
            echo "ERROR: FINAL_STEREO_MODE=single requires at least" >&2
            echo "one entry in FINAL_SINGLE_PAIRS." >&2
            return 1 2>/dev/null || exit 1
        fi

        ACTIVE_SINGLE_PAIRS=("${FINAL_SINGLE_PAIRS[@]}")
        ;;

    dual)
        if [[ "${acquisition_mode}" != "tri_stereo" ]]; then
            echo "ERROR: FINAL_STEREO_MODE=dual requires" >&2
            echo "acquisition_mode=tri_stereo." >&2
            return 1 2>/dev/null || exit 1
        fi

        if (( ${#FINAL_DUAL_CONFIGURATIONS[@]} == 0 )); then
            echo "ERROR: FINAL_STEREO_MODE=dual requires at least" >&2
            echo "one entry in FINAL_DUAL_CONFIGURATIONS." >&2
            return 1 2>/dev/null || exit 1
        fi

        ACTIVE_DUAL_CONFIGURATIONS=(
            "${FINAL_DUAL_CONFIGURATIONS[@]}"
        )
        ;;

    tri)
        if [[ "${acquisition_mode}" != "tri_stereo" ]]; then
            echo "ERROR: FINAL_STEREO_MODE=tri requires" >&2
            echo "acquisition_mode=tri_stereo." >&2
            return 1 2>/dev/null || exit 1
        fi

        ACTIVE_SINGLE_PAIRS=(
            "A B"
            "A C"
            "B C"
        )

        TRI_MERGE_REQUIRED=true
        TRI_MERGE_PAIR_TAGS=(AB AC BC)
        TRI_MERGE_TAG="ABACBC"
        ;;

    *)
        echo "ERROR: Unsupported FINAL_STEREO_MODE:" >&2
        echo "  ${FINAL_STEREO_MODE}" >&2
        echo "Allowed values: single, dual, tri" >&2
        return 1 2>/dev/null || exit 1
        ;;
esac


# Validate active single pairs and create tags such as AB or AC.
for pair_definition in "${ACTIVE_SINGLE_PAIRS[@]}"; do
    read -r -a pair_views <<< "${pair_definition}"

    if (( ${#pair_views[@]} != 2 )); then
        echo "ERROR: Invalid single-pair definition:" >&2
        echo "  ${pair_definition}" >&2
        echo 'Expected format: "A B"' >&2
        return 1 2>/dev/null || exit 1
    fi

    left_view="${pair_views[0]}"
    right_view="${pair_views[1]}"

    if [[ "${left_view}" == "${right_view}" ]]; then
        echo "ERROR: A single pair must use two different views:" >&2
        echo "  ${pair_definition}" >&2
        return 1 2>/dev/null || exit 1
    fi

    for requested_view in "${left_view}" "${right_view}"; do
        if ! view_is_available "${requested_view}"; then
            echo "ERROR: View ${requested_view} is not available." >&2
            echo "Available views: ${VIEW_IDS[*]}" >&2
            return 1 2>/dev/null || exit 1
        fi
    done

    ACTIVE_SINGLE_PAIR_TAGS+=("${left_view}${right_view}")
done


# Validate active ordered dual configurations and create tags
# such as ABC, BAC, or CAB.
for dual_definition in "${ACTIVE_DUAL_CONFIGURATIONS[@]}"; do
    read -r -a dual_views <<< "${dual_definition}"

    if (( ${#dual_views[@]} != 3 )); then
        echo "ERROR: Invalid dual configuration:" >&2
        echo "  ${dual_definition}" >&2
        echo 'Expected format: "A B C"' >&2
        return 1 2>/dev/null || exit 1
    fi

    first_view="${dual_views[0]}"
    second_view="${dual_views[1]}"
    third_view="${dual_views[2]}"

    if [[
        "${first_view}" == "${second_view}"
        || "${first_view}" == "${third_view}"
        || "${second_view}" == "${third_view}"
    ]]; then
        echo "ERROR: A dual configuration must use three" >&2
        echo "different views:" >&2
        echo "  ${dual_definition}" >&2
        return 1 2>/dev/null || exit 1
    fi

    for requested_view in \
        "${first_view}" \
        "${second_view}" \
        "${third_view}"
    do
        if ! view_is_available "${requested_view}"; then
            echo "ERROR: View ${requested_view} is not available." >&2
            echo "Available views: ${VIEW_IDS[*]}" >&2
            return 1 2>/dev/null || exit 1
        fi
    done

    ACTIVE_DUAL_CONFIGURATION_TAGS+=(
        "${first_view}${second_view}${third_view}"
    )
done



# ============================================================
# 5. AUTOMATIC REFERENCE-TOPOGRAPHY SETTINGS
# ============================================================

ALIGNMENT_DEM="${BASE_DIR}/${ALIGNMENT_DEM_RELATIVE}"
MAPPROJECT_DEM="${BASE_DIR}/${MAPPROJECT_DEM_RELATIVE}"
proj="EPSG:${TARGET_EPSG}"


# ============================================================
# 7. AUTOMATIC GENERAL OUTPUT DIRECTORIES
# ============================================================

OUTPUT_ROOT="${BASE_DIR}/${INPUT_TYPE}_data"
LOG_DIR="${OUTPUT_ROOT}/asp_logs"
ASP_OUTPUT_DIR="${OUTPUT_ROOT}/asp_out"
DSM_OUTPUT_DIR="${ASP_OUTPUT_DIR}/dems"

mkdir -p \
    "${LOG_DIR}" \
    "${ASP_OUTPUT_DIR}" \
    "${DSM_OUTPUT_DIR}"


# ============================================================
# 7A. AUTOMATIC FINAL POINT-CLOUD AND DSM DIRECTORIES
# ============================================================
#
# DSM_OUTPUT_DIR remains dedicated to preliminary processing
# products. Final point clouds and final DSMs are separated.
# ============================================================

FINAL_POINT_CLOUD_OUTPUT_DIR="${ASP_OUTPUT_DIR}/${FINAL_POINT_CLOUD_FOLDER_NAME}"

TRI_MERGED_POINT_CLOUD_OUTPUT_DIR="${FINAL_POINT_CLOUD_OUTPUT_DIR}/merged"

FINAL_DSM_OUTPUT_DIR="${ASP_OUTPUT_DIR}/${FINAL_DSM_FOLDER_NAME}"

mkdir -p \
    "${FINAL_POINT_CLOUD_OUTPUT_DIR}" \
    "${TRI_MERGED_POINT_CLOUD_OUTPUT_DIR}" \
    "${FINAL_DSM_OUTPUT_DIR}"


# ============================================================
# 8. AUTOMATIC INITIAL BUNDLE-ADJUSTMENT PATHS
# ============================================================

BA_OUTPUT_DIR="${ASP_OUTPUT_DIR}/ba_${VIEW_TAG}"
BA_PREFIX="${BA_OUTPUT_DIR}/${VIEW_TAG}"
BA_LOG="${LOG_DIR}/ba.${VIEW_TAG}.log"

BA_ADJUST_FILES=()
for image in "${IMAGE_FILES[@]}"; do
    image_stem="$(basename "${image}" .tif)"
    BA_ADJUST_FILES+=("${BA_PREFIX}-${image_stem}.adjust")
done


# ============================================================
# 9. AUTOMATIC PRELIMINARY STEREO PAIR
# ============================================================

case "${acquisition_mode}" in
    stereo)
        PRELIM_LEFT_INDEX=0
        PRELIM_RIGHT_INDEX=1
        ;;
    tri_stereo)
        PRELIM_LEFT_INDEX=0
        PRELIM_RIGHT_INDEX=2
        ;;
esac

PRELIM_LEFT_VIEW="${VIEW_IDS[$PRELIM_LEFT_INDEX]}"
PRELIM_RIGHT_VIEW="${VIEW_IDS[$PRELIM_RIGHT_INDEX]}"
PRELIM_PAIR_TAG="${PRELIM_LEFT_VIEW}${PRELIM_RIGHT_VIEW}"

PRELIM_LEFT_IMAGE="${IMAGE_FILES[$PRELIM_LEFT_INDEX]}"
PRELIM_RIGHT_IMAGE="${IMAGE_FILES[$PRELIM_RIGHT_INDEX]}"
PRELIM_LEFT_RPC="${RPC_FILES[$PRELIM_LEFT_INDEX]}"
PRELIM_RIGHT_RPC="${RPC_FILES[$PRELIM_RIGHT_INDEX]}"
PRELIM_LEFT_ADJUST="${BA_ADJUST_FILES[$PRELIM_LEFT_INDEX]}"
PRELIM_RIGHT_ADJUST="${BA_ADJUST_FILES[$PRELIM_RIGHT_INDEX]}"

PRELIM_STEREO_DIR="${DSM_OUTPUT_DIR}/stereo_preliminary_${PRELIM_PAIR_TAG}"
PRELIM_PREFIX="${PRELIM_STEREO_DIR}/preliminary"
PRELIM_LOG="${LOG_DIR}/preliminary_stereo.${PRELIM_PAIR_TAG}.log"
PRELIM_POINT_CLOUD="${PRELIM_PREFIX}-PC.tif"


# ============================================================
# 10. AUTOMATIC PRELIMINARY DEM PATHS
# ============================================================

PRELIM_DEM_PREFIX="${DSM_OUTPUT_DIR}/preliminary_${PRELIM_PAIR_TAG}"
PRELIM_DEM="${PRELIM_DEM_PREFIX}-DEM.tif"
PRELIM_DEM_LOG="${LOG_DIR}/preliminary_point2dem.${PRELIM_PAIR_TAG}.log"


# ============================================================
# 11. AUTOMATIC pc_align PATHS
# ============================================================

ALIGN_OUTPUT_DIR="${DSM_OUTPUT_DIR}/align"
ALIGN_PREFIX="${ALIGN_OUTPUT_DIR}/preliminary_${PRELIM_PAIR_TAG}_to_LiDAR"
ALIGN_TRANSFORM="${ALIGN_PREFIX}-transform.txt"
ALIGN_INVERSE_TRANSFORM="${ALIGN_PREFIX}-inverse-transform.txt"
ALIGN_LOG="${LOG_DIR}/preliminary_pc_align.${PRELIM_PAIR_TAG}.log"


# ============================================================
# 12. AUTOMATIC TRANSFORMED CAMERA PATHS
# ============================================================

ALIGNED_BA_OUTPUT_DIR="${ASP_OUTPUT_DIR}/ba_aligned_${VIEW_TAG}"
ALIGNED_BA_PREFIX="${ALIGNED_BA_OUTPUT_DIR}/${VIEW_TAG}"
ALIGNED_BA_LOG="${LOG_DIR}/ba_aligned.${VIEW_TAG}.log"

ALIGNED_ADJUST_FILES=()
for image in "${IMAGE_FILES[@]}"; do
    image_stem="$(basename "${image}" .tif)"
    ALIGNED_ADJUST_FILES+=("${ALIGNED_BA_PREFIX}-${image_stem}.adjust")
done


# ============================================================
# 13. AUTOMATIC MAPPROJECT OUTPUT PATHS
# ============================================================

MAPPROJECT_OUTPUT_DIR="${ASP_OUTPUT_DIR}/mapproject"
mkdir -p "${MAPPROJECT_OUTPUT_DIR}"

MAPPROJECTED_IMAGES=()
MAPPROJECT_LOGS=()

for view in "${VIEW_IDS[@]}"; do
    MAPPROJECTED_IMAGES+=(
        "${MAPPROJECT_OUTPUT_DIR}/${view}_${area_name}_${rawRes_meters}m_baL50.tif"
    )
    MAPPROJECT_LOGS+=("${LOG_DIR}/mapproject.${view}.log")
done


# ============================================================
# 14. VALIDATE PREPARED INPUT IMAGES
# ============================================================

if [[ ! -d "${INPUT_DIR}" ]]; then
    echo "ERROR: Input directory does not exist:" >&2
    echo "  ${INPUT_DIR}" >&2
    return 1 2>/dev/null || exit 1
fi

for index in "${!VIEW_IDS[@]}"; do
    view="${VIEW_IDS[$index]}"
    image="${IMAGE_FILES[$index]}"
    rpc="${RPC_FILES[$index]}"

    if [[ ! -s "${image}" ]]; then
        echo "ERROR: Missing or empty ${view} image:" >&2
        echo "  ${image}" >&2
        return 1 2>/dev/null || exit 1
    fi

    if [[ ! -s "${rpc}" ]]; then
        echo "ERROR: Missing or empty ${view} RPC file:" >&2
        echo "  ${rpc}" >&2
        return 1 2>/dev/null || exit 1
    fi
done


# ============================================================
# 15. GENERIC FILE-VALIDATION FUNCTION
# ============================================================

require_files() {
    local required_file

    for required_file in "$@"; do
        if [[ ! -s "${required_file}" ]]; then
            echo "ERROR: Required file is missing or empty:" >&2
            echo "  ${required_file}" >&2
            return 1
        fi
    done
}


# ============================================================
# 16. GENERIC OUTPUT-PREPARATION FUNCTION
# ============================================================

prepare_output_prefix() {
    local output_prefix="$1"
    local log_file="${2:-}"
    local output_directory
    local outputs_exist=false

    output_directory="$(dirname "${output_prefix}")"
    mkdir -p "${output_directory}"

    if [[ -n "${log_file}" ]]; then
        mkdir -p "$(dirname "${log_file}")"
    fi

    shopt -s nullglob
    local existing_outputs=("${output_prefix}"*)
    shopt -u nullglob

    if (( ${#existing_outputs[@]} > 0 )); then
        outputs_exist=true
    fi

    if [[ -n "${log_file}" && -e "${log_file}" ]]; then
        outputs_exist=true
    fi

    if [[ "${outputs_exist}" == true ]]; then
        if [[ "${OVERWRITE_OUTPUTS}" == true ]]; then
            echo "Removing existing outputs for:"
            echo "  ${output_prefix}"

            if (( ${#existing_outputs[@]} > 0 )); then
                rm -rf -- "${existing_outputs[@]}"
            fi

            if [[ -n "${log_file}" ]]; then
                rm -f -- "${log_file}"
            fi
        else
            echo "ERROR: Outputs already exist:" >&2
            echo "  ${output_prefix}*" >&2
            echo "Set OVERWRITE_OUTPUTS=true to replace them." >&2
            return 1
        fi
    fi
}


# ============================================================
# 17. OPTIONAL INPUT DISPLAY
# ============================================================

show_selected_images() {
    echo ""
    echo "============================================================"
    echo "SELECTED INPUT IMAGES"
    echo "============================================================"
    echo "Area             : ${area_name}"
    echo "Sensor           : ${sensor_type}"
    echo "Acquisition mode : ${acquisition_mode}"
    echo "Input type       : ${INPUT_TYPE}"
    echo ""

    for index in "${!VIEW_IDS[@]}"; do
        echo "${VIEW_IDS[$index]} image : ${IMAGE_FILES[$index]}"
        echo "${VIEW_IDS[$index]} RPC   : ${RPC_FILES[$index]}"
    done

    echo "============================================================"
    echo ""
}

show_reference_topography() {
    echo ""
    echo "============================================================"
    echo "REFERENCE TOPOGRAPHY"
    echo "============================================================"
    echo "Alignment LiDAR DSM  : ${ALIGNMENT_DEM}"
    echo "Mapproject LiDAR DSM : ${MAPPROJECT_DEM}"
    echo "Target CRS           : ${proj}"
    echo "============================================================"
    echo ""
}


show_final_processing_configuration() {
    echo ""
    echo "============================================================"
    echo "FINAL POINT-CLOUD AND DSM CONFIGURATION"
    echo "============================================================"
    echo "Stereo mode          : ${FINAL_STEREO_MODE}"
    echo "Stereo algorithm     : ${FINAL_STEREO_ALGORITHM}"
    echo "Correlation kernels  : ${FINAL_CKERNELS[*]}"
    echo "Subpixel kernels     : ${FINAL_SKERNELS[*]}"
    echo "Point-cloud folder   : ${FINAL_POINT_CLOUD_OUTPUT_DIR}"
    echo "Final DSM folder     : ${FINAL_DSM_OUTPUT_DIR}"
    echo "DSM resolution       : ${FINAL_DSM_RESOLUTION} m"
    echo "Triangulation limit  : ${FINAL_MAX_VALID_TRIANGULATION_ERROR} m"
    echo "Error image          : ${FINAL_DSM_CREATE_ERROR_IMAGE}"
    echo "============================================================"
    echo ""
}


# ============================================================
# 18. DISPLAY ONLY WHEN EXECUTED DIRECTLY
# ============================================================

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    show_selected_images
    show_reference_topography
    show_final_processing_configuration

    echo "Initial BA prefix       : ${BA_PREFIX}"
    echo "Preliminary pair        : ${PRELIM_PAIR_TAG}"
    echo "Preliminary prefix      : ${PRELIM_PREFIX}"
    echo "Preliminary point cloud : ${PRELIM_POINT_CLOUD}"
    echo "Preliminary DEM         : ${PRELIM_DEM}"
    echo "Alignment prefix        : ${ALIGN_PREFIX}"
    echo "Alignment transform     : ${ALIGN_TRANSFORM}"
    echo "Aligned BA prefix       : ${ALIGNED_BA_PREFIX}"
    echo "Mapproject DEM          : ${MAPPROJECT_DEM}"
    echo "Mapproject directory    : ${MAPPROJECT_OUTPUT_DIR}"
    echo ""
fi