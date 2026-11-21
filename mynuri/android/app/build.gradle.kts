import java.util.Properties

plugins {
    id("com.android.application")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

val releaseSigningProperties = Properties()
val releaseSigningFile = rootProject.file("key.properties")
if (releaseSigningFile.isFile) {
    releaseSigningFile.inputStream().use { releaseSigningProperties.load(it) }
}
val releaseStoreFile = releaseSigningProperties.getProperty("storeFile")?.let { rootProject.file(it) }
val hasReleaseSigning = releaseStoreFile?.isFile == true &&
    listOf("keyAlias", "keyPassword", "storePassword").all {
        !releaseSigningProperties.getProperty(it).isNullOrBlank()
    }

android {
    namespace = "com.example.mynuri"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    if (providers.environmentVariable("NURI_APP_ROLE").orNull == "assistant") {
        sourceSets.getByName("main").jniLibs.srcDir("src/assistantOffline/jniLibs")
        packaging.jniLibs.useLegacyPackaging = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    defaultConfig {
        // TODO: Specify your own unique Application ID (https://developer.android.com/studio/build/application-id.html).
        applicationId = "com.mynuri.share"
        manifestPlaceholders["appUpdateRole"] =
            if (providers.environmentVariable("NURI_APP_ROLE").orNull == "assistant") "assistant"
            else if (providers.environmentVariable("NURI_APP_ROLE").orNull == "nuri") "nuri"
            else "manual"
        manifestPlaceholders["restoreCommandWorker"] =
            (providers.environmentVariable("NURI_APP_ROLE").orNull == "nuri").toString()
        manifestPlaceholders["appLabel"] =
            if (providers.environmentVariable("NURI_APP_ROLE").orNull == "assistant") "도우미" else "누리"
        manifestPlaceholders["appIcon"] =
            "@mipmap/ic_launcher"
        // You can update the following values to match your application needs.
        // For more information, see: https://flutter.dev/to/review-gradle-config.
        minSdk = flutter.minSdkVersion
        targetSdk = flutter.targetSdkVersion
        // Uses the version code from pubspec.yaml. When using split APKs, 1000 * ABI_VERSION
        // is added automatically by Flutter. (https://developer.android.com/studio/build/configure-apk-splits#configure-APK-versions)
        // You can force using the value of versionCode by specifying the `-P force-version-code-ignoring-abi=true`
        // flag during build.
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    signingConfigs {
        create("release") {
            if (hasReleaseSigning) {
                storeFile = releaseStoreFile
                storePassword = releaseSigningProperties.getProperty("storePassword")
                keyAlias = releaseSigningProperties.getProperty("keyAlias")
                keyPassword = releaseSigningProperties.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            signingConfig = signingConfigs.getByName("release")
        }
    }
}

val checkReleaseSigning = tasks.register("checkReleaseSigning") {
    doLast {
        check(hasReleaseSigning) {
            "Release builds require an existing keystore and storeFile, storePassword, keyAlias, keyPassword in android/key.properties."
        }
    }
}
tasks.matching { it.name == "preReleaseBuild" }.configureEach {
    dependsOn(checkReleaseSigning)
}

kotlin {
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}

dependencies {
    implementation("com.microsoft.onnxruntime:onnxruntime-android:1.18.0")
}

flutter {
    source = "../.."
}
