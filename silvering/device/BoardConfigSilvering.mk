# Board settings that CAF keeps in its proprietary
# $(QCPATH)/common/msm8916/BoardConfigVendor.mk, which an open build lacks.
# build.sh hands this file to the build as ANDROID_BUILDSPEC, which
# build/core/config.mk reads before the product and board configuration; no
# CAF file is changed. (Pointing QCPATH here instead would also put
# proprietary jars on the boot class path: msm8916_64.mk.)
#
# Wi-Fi (WCNSS/Pronto). Without these the build makes no wpa_supplicant or
# hostapd and its libhardware_legacy never loads the Wi-Fi driver. The values
# are the factory image's: its libhardware_legacy.so loads
# /system/lib/modules/wlan.ko as module "wlan" and switches the firmware with
# "sta", "ap" and "p2p" through /sys/module/wlan/parameters/fwpath (the
# default), with no module argument, and it ships libwifi-hal-qcom.so.
BOARD_HAS_QCOM_WLAN := true
BOARD_WLAN_DEVICE := qcwcn
WPA_SUPPLICANT_VERSION := VER_0_8_X
BOARD_WPA_SUPPLICANT_DRIVER := NL80211
BOARD_HOSTAPD_DRIVER := NL80211
BOARD_WPA_SUPPLICANT_PRIVATE_LIB := lib_driver_cmd_$(BOARD_WLAN_DEVICE)
BOARD_HOSTAPD_PRIVATE_LIB := lib_driver_cmd_$(BOARD_WLAN_DEVICE)
WIFI_DRIVER_MODULE_PATH := "/system/lib/modules/wlan.ko"
WIFI_DRIVER_MODULE_NAME := "wlan"
WIFI_DRIVER_FW_PATH_STA := "sta"
WIFI_DRIVER_FW_PATH_AP := "ap"
WIFI_DRIVER_FW_PATH_P2P := "p2p"
