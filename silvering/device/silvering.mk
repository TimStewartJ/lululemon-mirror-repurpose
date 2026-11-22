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

# Platform, shared, media and default apps are signed with the build host's
# own keys (build.sh makes them once and passes their folder), never AOSP's
# public test keys, whose platform key would let anyone sign an app with
# system rights. The build is tagged dev-keys.
ifeq ($(SILVERING_KEYS),)
$(error SILVERING_KEYS is not set: build Silvering with build.sh)
endif
PRODUCT_DEFAULT_DEV_CERTIFICATE := $(SILVERING_KEYS)/releasekey

# The board's own files (Qualcomm's libraries, the unit's kernel modules and
# configuration) are not part of the product: build.sh lays them over the
# finished /system with stage-image.sh, so they always win.
