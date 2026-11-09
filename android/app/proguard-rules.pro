# Healdragg: приложение — тонкая обёртка над WebView, специальных правил не нужно.
-keepclassmembers class * {
    @android.webkit.JavascriptInterface <methods>;
}
