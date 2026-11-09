import java.io.File
import java.io.FileOutputStream
import java.security.MessageDigest
import java.security.SecureRandom
import javax.crypto.Cipher
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Движок healdragg целиком (пакет + install.sh + pyproject) пакуется внутрь APK.
// В APK он лежит зашифрованным (AES-GCM): распакованный apk не показывает исходники.
val engineAssets = layout.buildDirectory.dir("generated/engineAssets")
val engineAssetsDir = engineAssets.get().asFile
val repoRoot = rootProject.projectDir.parentFile

/**
 * Отпечаток движка: хеш исходников. Попадает в BuildConfig.ENGINE_ID, по нему
 * Termux-скрипт понимает, что APK принёс новую версию движка, и переустанавливает её.
 */
fun engineFingerprint(): String {
    val digest = MessageDigest.getInstance("SHA-256")
    val files = mutableListOf<File>()
    File(repoRoot, "healdragg").walkTopDown()
        .filter { it.isFile && "__pycache__" !in it.path && !it.name.endsWith(".pyc") }
        .forEach { files.add(it) }
    files.add(File(repoRoot, "pyproject.toml"))
    files.add(File(repoRoot, "install.sh"))
    files.sortedBy { it.path }.forEach { file ->
        digest.update(file.relativeTo(repoRoot).invariantSeparatorsPath.toByteArray())
        digest.update(0.toByte())
        digest.update(file.readBytes())
    }
    return digest.digest().joinToString("") { "%02x".format(it) }.take(16)
}

val engineStamp = engineFingerprint()

android {
    namespace = "net.syslikoff.healdragg"
    compileSdk = 34

    defaultConfig {
        applicationId = "net.syslikoff.healdragg"
        minSdk = 24
        targetSdk = 34
        versionCode = 3
        versionName = "1.1.1"
        resourceConfigurations += listOf("ru", "en")
        buildConfigField("String", "ENGINE_ID", "\"$engineStamp\"")
    }

    sourceSets.getByName("main").assets.srcDir(engineAssetsDir)

    buildTypes {
        release {
            // R8: убираем имена классов/методов, мёртвый код и неиспользуемые ресурсы
            isMinifyEnabled = true
            isShrinkResources = true
            isDebuggable = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            // подпись debug-ключом, чтобы APK из CI сразу ставился; для стора — свой keystore
            signingConfig = signingConfigs.getByName("debug")
        }
        debug {
            isMinifyEnabled = false
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

val bundleEngine = tasks.register("bundleEngine") {
    val sources = File(repoRoot, "healdragg")
    val extras = listOf(File(repoRoot, "pyproject.toml"), File(repoRoot, "install.sh"))
    inputs.dir(sources).withPropertyName("engineSources")
    inputs.files(extras).withPropertyName("engineExtras")
    outputs.file(engineAssets.map { it.file("engine.bin") }).withPropertyName("engineArchive")

    doLast {
        val outDir = engineAssetsDir
        outDir.mkdirs()
        val tarGz = File(outDir, "engine.tar.gz")
        tarGz.delete()
        val proc = ProcessBuilder(
            "tar", "-czf", tarGz.absolutePath,
            "--exclude=__pycache__", "--exclude=*.pyc", "--exclude=.venv",
            "healdragg", "pyproject.toml", "install.sh",
        ).directory(repoRoot).start()
        val err = proc.errorStream.bufferedReader().readText()
        check(proc.waitFor() == 0 && tarGz.isFile) { "не удалось упаковать движок: $err" }

        // Ключ выводится из строки; в приложении — та же формула.
        val key = MessageDigest.getInstance("SHA-256")
            .digest("healdragg-engine-v1".toByteArray())
        val nonce = ByteArray(12).also { SecureRandom().nextBytes(it) }
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, SecretKeySpec(key, "AES"), GCMParameterSpec(128, nonce))
        val sealed = cipher.doFinal(tarGz.readBytes())
        FileOutputStream(File(outDir, "engine.bin")).use { out ->
            out.write(nonce)
            out.write(sealed)
        }
        tarGz.delete()
        println("движок: engine.bin ${File(outDir, "engine.bin").length()} B")
    }
}

tasks.named("preBuild").configure { dependsOn(bundleEngine) }

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.swiperefreshlayout:swiperefreshlayout:1.1.0")
    implementation("androidx.webkit:webkit:1.11.0")
}
