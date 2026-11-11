import java.io.File

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

// Python-движок живёт внутри APK (Chaquopy): Termux, pip и Rust на телефоне
// больше не участвуют. Исходники пакета healdragg перед сборкой раскладываются
// в build/pythonSrc и попадают в APK уже скомпилированными в .pyc.
val repoRoot = rootProject.projectDir.parentFile
val stagedPython = layout.buildDirectory.dir("pythonSrc").get().asFile

fun stageEngineSources() {
    val target = File(stagedPython, "healdragg")
    target.deleteRecursively()
    File(repoRoot, "healdragg").copyRecursively(target, overwrite = true)
    target.walkBottomUp()
        .filter { it.isDirectory && (it.name == "__pycache__" || it.name == "logs") }
        .forEach { it.deleteRecursively() }
}

stageEngineSources()

android {
    namespace = "net.syslikoff.healdragg"
    compileSdk = 34

    defaultConfig {
        applicationId = "net.syslikoff.healdragg"
        minSdk = 24
        targetSdk = 34
        versionCode = 26
        versionName = "1.10.1"
        resourceConfigurations += listOf("ru", "en")
        ndk {
            // Chaquopy: только 64-битные ABI (Python 3.13 их и поддерживает)
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
    }

    // Один ключ на debug и release: обновления ставятся поверх старой версии,
    // подпись больше не меняется от сборки к сборке.
    // Ключ только для сайдлоада; для Google Play нужен отдельный.
    signingConfigs {
        create("shared") {
            storeFile = file("../keystore/healdragg.p12")
            storePassword = "healdragg"
            keyAlias = "healdragg"
            keyPassword = "healdragg"
        }
    }

    buildTypes {
        release {
            // R8: убираем имена классов/методов, мёртвый код и неиспользуемые ресурсы
            isMinifyEnabled = true
            isShrinkResources = true
            isDebuggable = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = signingConfigs.getByName("shared")
        }
        debug {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("shared")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        buildConfig = true
        compose = true
    }
    composeOptions {
        kotlinCompilerExtensionVersion = "1.5.14"
    }
    packaging {
        resources.excludes += setOf("META-INF/*.kotlin_module")
    }
}

chaquopy {
    defaultConfig {
        version = "3.13"
        // .pyc обязателен: исходники .py в APK не попадают
        pyc {
            src = true
        }
        pip {
            // весь стек — чистый Python, без нативных сборок
            // pyaes выходит только исходниками, поэтому лежит готовым wheel'ом
            install("wheels/pyaes-1.6.1-py3-none-any.whl")
            install("telethon==1.45.0")
            install("starlette>=0.37")
            install("uvicorn>=0.27")
            install("rich>=13,<15")
            install("python-dotenv>=1.0")
            // база часовых поясов: на Android zoneinfo без неё не работает
            install("tzdata")
        }
    }
    sourceSets {
        getByName("main") {
            srcDir(stagedPython.absolutePath)
        }
    }
}

dependencies {
    testImplementation("junit:junit:4.13.2")
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.work:work-runtime-ktx:2.9.0")

    // нативный UI
    implementation(platform("androidx.compose:compose-bom:2024.06.00"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material")
    implementation("androidx.compose.material:material-icons-core")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.activity:activity-compose:1.9.0")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.2")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.2")
    implementation("androidx.lifecycle:lifecycle-viewmodel-ktx:2.8.2")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
    implementation("com.airbnb.android:lottie-compose:6.4.1")
    debugImplementation("androidx.compose.ui:ui-tooling")
}
