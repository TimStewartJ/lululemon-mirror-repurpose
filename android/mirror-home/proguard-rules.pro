# Mirror Home's builds drop the code that nothing uses. Most of the app is
# libraries, and Android 6 compiles every method of an app while installing
# it, on a Mirror with 1 GB of memory.

# Names stay as written, so that a crash report reads like the source.
-dontobfuscate
-keepattributes SourceFile,LineNumberTable
