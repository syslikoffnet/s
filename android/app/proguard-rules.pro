# Healdragg: Release — R8 убирает имена классов, мёртвый код и ресурсы.
# Приложение почти целиком WebView, поэтому специальных правил немного.
-keep class net.syslikoff.healdragg.MainActivity { *; }
-keepclassmembers class * {
    @android.webkit.JavascriptInterface <methods>;
}

# не тащим в APK имена исходников и отладочную информацию
-renamesourcefileattribute SourceFile
-keepattributes SourceFile,LineNumberTable

# немного агрессивнее: склеиваем классы и убираем неиспользуемые методы
-allowaccessmodification
-optimizationpasses 5
