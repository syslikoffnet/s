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
        versionCode = 5
        versionName = "1.2.0"
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
    }
    packaging {
        resources.excludes += setOf("META-INF/*.kotlin_module")
    }
}

chaquopy {
    defaultConfig {
        version = "3.13"
        pip {
            // весь стек — чистый Python, без нативных сборок
            install("telethon>=1.36,<2")
            install("starlette>=0.37")
            install("uvicorn>=0.27")
            install("rich>=13,<15")
            install("python-dotenv>=1.0")
        }
    }
    sourceSets {
        getByName("main") {
            srcDir(stagedPython.absolutePath)
        }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.swiperefreshlayout:swiperefreshlayout:1.1.0")
    implementation("androidx.webkit:webkit:1.11.0")
    implementation("androidx.work:work-runtime-ktx:2.9.0")
}
