# Mirror Home's builds drop the code that nothing uses. Most of the app is
# libraries, and Android 6 compiles every method of an app while installing
# it, on a Mirror with 1 GB of memory.

# Names stay as written, so that a crash report reads like the source.
-dontobfuscate
-keepattributes SourceFile,LineNumberTable

# The speech recogniser. JNA's native half looks its Java classes, fields and
# methods up by name, and binds Vosk's library to LibVosk's methods the same way.
-keep class com.sun.jna.** { *; }
-keep class * implements com.sun.jna.** { *; }
-keep class org.vosk.** { *; }
# JNA's desktop-only window helpers, which Android has no classes for.
-dontwarn java.awt.**
