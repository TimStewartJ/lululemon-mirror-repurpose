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

# Files taken from the owner's own unit at build time. Never committed.
-include vendor/mirror/unit/unit.mk
