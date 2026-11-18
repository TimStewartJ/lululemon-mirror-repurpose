# Mirror Home and the OTA supervisor as signed prebuilts. build.sh copies them
# into prebuilt/ from $SILVERING_ROOT/apps, with Mirror Home's arm64 libraries
# unpacked into prebuilt/MirrorHome-lib/; a module whose APK is absent is
# left out.
LOCAL_PATH := $(call my-dir)

ifneq ($(wildcard $(LOCAL_PATH)/prebuilt/MirrorHome.apk),)
include $(CLEAR_VARS)
LOCAL_MODULE := MirrorHome
LOCAL_MODULE_CLASS := APPS
LOCAL_MODULE_TAGS := optional
LOCAL_MODULE_SUFFIX := $(COMMON_ANDROID_PACKAGE_SUFFIX)
LOCAL_SRC_FILES := prebuilt/MirrorHome.apk
LOCAL_CERTIFICATE := PRESIGNED
LOCAL_DEX_PREOPT := false
LOCAL_OVERRIDES_PACKAGES := Launcher2 Launcher3
LOCAL_MULTILIB := 64
LOCAL_PREBUILT_JNI_LIBS := $(patsubst $(LOCAL_PATH)/%,%,\
    $(wildcard $(LOCAL_PATH)/prebuilt/MirrorHome-lib/*.so))
include $(BUILD_PREBUILT)
endif

ifneq ($(wildcard $(LOCAL_PATH)/prebuilt/MirrorUpdater.apk),)
include $(CLEAR_VARS)
LOCAL_MODULE := MirrorUpdater
LOCAL_MODULE_CLASS := APPS
LOCAL_MODULE_TAGS := optional
LOCAL_MODULE_SUFFIX := $(COMMON_ANDROID_PACKAGE_SUFFIX)
LOCAL_SRC_FILES := prebuilt/MirrorUpdater.apk
LOCAL_CERTIFICATE := PRESIGNED
LOCAL_DEX_PREOPT := false
include $(BUILD_PREBUILT)
endif
