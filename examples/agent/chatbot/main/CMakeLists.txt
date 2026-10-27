file(GLOB_RECURSE SRCS "${CMAKE_CURRENT_LIST_DIR}/*.cpp" "${CMAKE_CURRENT_LIST_DIR}/*.c")
set(EMBEDDED_FILES "")

if (CONFIG_EXAMPLE_AGENTS_COZE_PRIVATE_KEY_FILE_PATH)
    if (NOT EXISTS ${CMAKE_CURRENT_LIST_DIR}/${CONFIG_EXAMPLE_AGENTS_COZE_PRIVATE_KEY_FILE_PATH})
        message(FATAL_ERROR "Coze Private Key file not found: ${CONFIG_EXAMPLE_AGENTS_COZE_PRIVATE_KEY_FILE_PATH}")
    endif()
    list(APPEND EMBEDDED_FILES ${CONFIG_EXAMPLE_AGENTS_COZE_PRIVATE_KEY_FILE_PATH})
    message(STATUS "Coze Private Key Path: ${CONFIG_EXAMPLE_AGENTS_COZE_PRIVATE_KEY_FILE_PATH}")
endif()

idf_component_register(
    SRCS ${SRCS}
    INCLUDE_DIRS "."
    PRIV_REQUIRES esp_http_client esp_http_server esp_timer
    EMBED_TXTFILES ${EMBEDDED_FILES}
)

littlefs_create_partition_image(littlefs_data ../littlefs FLASH_IN_PROJECT)

set(ASSETS_PARTITION_NAME "anim_icon")
set(ASSETS_BIN_NAME ${ASSETS_PARTITION_NAME})
set(SUPPORTED_RESOLUTIONS "320_240" "360_360" "1024_600")
set(DEFAULT_RESOLUTION "320_240")
set(RESOLUTION "${CONFIG_BROOKESIA_HAL_ADAPTOR_DISPLAY_LCD_PANEL_H_RES}_${CONFIG_BROOKESIA_HAL_ADAPTOR_DISPLAY_LCD_PANEL_V_RES}")
# Use an exact asset resolution when available. Otherwise, choose the largest supported
# resolution that does not exceed the configured panel size, then fall back to the default.
if (NOT RESOLUTION IN_LIST SUPPORTED_RESOLUTIONS)
    set(SELECTED_RESOLUTION "")
    set(SELECTED_RESOLUTION_SCORE "")
    foreach(candidate_resolution IN LISTS SUPPORTED_RESOLUTIONS)
        string(REPLACE "_" ";" candidate_resolution_parts "${candidate_resolution}")
        list(GET candidate_resolution_parts 0 candidate_h_res)
        list(GET candidate_resolution_parts 1 candidate_v_res)

        if (
            (candidate_h_res LESS_EQUAL CONFIG_BROOKESIA_HAL_ADAPTOR_DISPLAY_LCD_PANEL_H_RES) AND
            (candidate_v_res LESS_EQUAL CONFIG_BROOKESIA_HAL_ADAPTOR_DISPLAY_LCD_PANEL_V_RES)
        )
            math(EXPR candidate_h_delta
                "${CONFIG_BROOKESIA_HAL_ADAPTOR_DISPLAY_LCD_PANEL_H_RES} - ${candidate_h_res}"
            )
            math(EXPR candidate_v_delta
                "${CONFIG_BROOKESIA_HAL_ADAPTOR_DISPLAY_LCD_PANEL_V_RES} - ${candidate_v_res}"
            )
            math(EXPR candidate_score "${candidate_h_delta} + ${candidate_v_delta}")

            if (SELECTED_RESOLUTION STREQUAL "")
                set(SELECTED_RESOLUTION "${candidate_resolution}")
                set(SELECTED_RESOLUTION_SCORE "${candidate_score}")
            elseif (candidate_score LESS SELECTED_RESOLUTION_SCORE)
                set(SELECTED_RESOLUTION "${candidate_resolution}")
                set(SELECTED_RESOLUTION_SCORE "${candidate_score}")
            endif()
        endif()
    endforeach()

    if (SELECTED_RESOLUTION STREQUAL "")
        message(
            WARNING
            "Unsupported asset resolution '${RESOLUTION}', using default '${DEFAULT_RESOLUTION}' "
            "(supported: ${SUPPORTED_RESOLUTIONS})"
        )
        set(RESOLUTION "${DEFAULT_RESOLUTION}")
    else()
        message(
            WARNING
            "Unsupported asset resolution '${RESOLUTION}', using nearest '${SELECTED_RESOLUTION}' "
            "(supported: ${SUPPORTED_RESOLUTIONS})"
        )
        set(RESOLUTION "${SELECTED_RESOLUTION}")
    endif()
endif()
message(STATUS "Asset Resolution: ${RESOLUTION}")
if(CONFIG_EXAMPLE_USE_DAFEIYU_EXPRESSIONS)
    set(DAFEIYU_ASSETS_DIR "${CMAKE_CURRENT_LIST_DIR}/../assets/dafeiyu")
    set(DAFEIYU_PACKER "${CMAKE_CURRENT_LIST_DIR}/../tools/build_dafeiyu_assets.py")
    set(DAFEIYU_VIEW_ARGS "")
    set(DAFEIYU_PROFILE_DIR "${DAFEIYU_ASSETS_DIR}/${RESOLUTION}")
    if(CONFIG_EXAMPLE_DAFEIYU_CLOSEUP_ENABLE)
        list(APPEND DAFEIYU_VIEW_ARGS --closeup)
        set(DAFEIYU_PROFILE_DIR "${DAFEIYU_ASSETS_DIR}/closeup/${RESOLUTION}")
    endif()
    get_component_path("esp_emote_assets" EMOTE_ASSETS_COMPONENT_DIR)
    partition_table_get_partition_info(ASSETS_PARTITION_SIZE
        "--partition-name ${ASSETS_PARTITION_NAME}" "size")
    if(CONFIG_EXAMPLE_DAFEIYU_SMOOTH_32M)
        if(NOT CONFIG_IDF_TARGET_ESP32S3 OR NOT CONFIG_ESPTOOLPY_FLASHSIZE_32MB OR NOT CONFIG_SPIRAM
           OR NOT CONFIG_SPIRAM_USE_MALLOC
           OR NOT CONFIG_EXAMPLE_DAFEIYU_CLOSEUP_ENABLE
           OR NOT CONFIG_BROOKESIA_HAL_ADAPTOR_DISPLAY_LCD_PANEL_H_RES EQUAL 360
           OR NOT CONFIG_BROOKESIA_HAL_ADAPTOR_DISPLAY_LCD_PANEL_V_RES EQUAL 360)
            message(FATAL_ERROR "Smooth Dafeiyu requires ESP32-S3, 32 MB flash, PSRAM in malloc heap, close-up and a 360x360 panel")
        endif()
        math(EXPR DAFEIYU_PARTITION_BYTES "${ASSETS_PARTITION_SIZE}")
        if(DAFEIYU_PARTITION_BYTES LESS 12582912)
            message(FATAL_ERROR "Smooth Dafeiyu needs a 12 MiB anim_icon partition; select partitions_32m.csv")
        endif()
        list(APPEND DAFEIYU_VIEW_ARGS --smooth)
        set(DAFEIYU_PROFILE_DIR "${DAFEIYU_ASSETS_DIR}/smooth/${RESOLUTION}")
    endif()
    file(GLOB_RECURSE DAFEIYU_ASSET_FILES CONFIGURE_DEPENDS
        "${DAFEIYU_PROFILE_DIR}/*.eaf"
        "${DAFEIYU_PROFILE_DIR}/*.json")
    file(GLOB_RECURSE EMOTE_SUPPORT_FILES CONFIGURE_DEPENDS
        "${EMOTE_ASSETS_COMPONENT_DIR}/font/*.bin"
        "${EMOTE_ASSETS_COMPONENT_DIR}/emoji_*/*.bin"
        "${EMOTE_ASSETS_COMPONENT_DIR}/emoji_*/listen.eaf"
        "${EMOTE_ASSETS_COMPONENT_DIR}/${RESOLUTION}/*.json")
    set(ASSETS_FILE "${CMAKE_BINARY_DIR}/${ASSETS_BIN_NAME}_dafeiyu.bin")
    add_custom_command(
        OUTPUT "${ASSETS_FILE}"
        COMMAND "${PYTHON}" "${DAFEIYU_PACKER}"
            --component "${EMOTE_ASSETS_COMPONENT_DIR}"
            --assets "${DAFEIYU_ASSETS_DIR}"
            --resolution "${RESOLUTION}"
            ${DAFEIYU_VIEW_ARGS}
            --name-length "${CONFIG_MMAP_FILE_NAME_LENGTH}"
            --partition-size "${ASSETS_PARTITION_SIZE}"
            --output "${ASSETS_FILE}"
        DEPENDS "${DAFEIYU_PACKER}" ${DAFEIYU_ASSET_FILES} ${EMOTE_SUPPORT_FILES}
        COMMENT "Building Dafeiyu expression assets"
        VERBATIM
    )
    add_custom_target(generated_dafeiyu_assets ALL DEPENDS "${ASSETS_FILE}")
    add_dependencies(flash generated_dafeiyu_assets)
else()
    set(ASSETS_FILE "${CMAKE_BINARY_DIR}/${ASSETS_BIN_NAME}.bin")
    build_speaker_assets_bin(${ASSETS_BIN_NAME} ${RESOLUTION} ${ASSETS_FILE} ${CONFIG_MMAP_FILE_NAME_LENGTH})
endif()
message(STATUS "Generated emote assets: ${ASSETS_FILE} -> ${ASSETS_PARTITION_NAME} partition")
esptool_py_flash_to_partition(flash ${ASSETS_PARTITION_NAME} "${ASSETS_FILE}")

target_compile_definitions(${COMPONENT_LIB} PRIVATE
    ASSETS_PARTITION_NAME="${ASSETS_PARTITION_NAME}"
)

if(DEFINED ENV{IDF_CI_BUILD})
    target_compile_definitions(${COMPONENT_LIB} PRIVATE
        IDF_CI_BUILD
    )
endif()

#
# The following WiFi credentials are used for CI test. Please ignore them if you are not running CI test.
#
if(DEFINED ENV{CI_TEST_WIFI_2_4G_AP1_SSID})
    target_compile_definitions(${COMPONENT_LIB} PRIVATE
        CI_TEST_WIFI_2_4G_AP1_SSID="$ENV{CI_TEST_WIFI_2_4G_AP1_SSID}"
    )
endif()
if(DEFINED ENV{CI_TEST_WIFI_2_4G_AP1_PSW})
    target_compile_definitions(${COMPONENT_LIB} PRIVATE
        CI_TEST_WIFI_2_4G_AP1_PSW="$ENV{CI_TEST_WIFI_2_4G_AP1_PSW}"
    )
endif()
