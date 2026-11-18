# Silvering for MSM8916 MIRROR units: Qualcomm's msm8916_64 product from the
# same CAF release as the factory image, with Mirror Home as the home screen.
# The board's own boot image and kernel stay; only system and userdata change.
$(call inherit-product, device/qcom/msm8916_64/msm8916_64.mk)

PRODUCT_NAME := silvering
PRODUCT_DEVICE := msm8916_64
PRODUCT_BRAND := mirror
PRODUCT_MODEL := MIRROR
PRODUCT_MANUFACTURER := Silvering

PRODUCT_PACKAGES += MirrorHome MirrorUpdater

# The board's own files (Qualcomm's libraries, the unit's kernel modules and
# configuration) are not part of the product: build.sh lays them over the
# finished /system with stage-image.sh, so they always win.
