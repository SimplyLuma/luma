import java.util.Properties
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

/**
 * The release signing key never lives in the repository. Release builds use the key named by
 * LUMA_CONNECT_SIGNING (a properties file with storeFile, storePassword, keyAlias and keyPassword),
 * defaulting to ~/.config/luma-connect-android/signing.properties; without one they fall back to
 * the local debug key and are only fit for testing.
 */
val releaseSigning: Properties? = (System.getenv("LUMA_CONNECT_SIGNING")
    ?: "${System.getProperty("user.home")}/.config/luma-connect-android/signing.properties")
    .let(::file).takeIf { it.isFile }?.let { source -> Properties().apply { source.inputStream().use(::load) } }

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
}

android {
    namespace = "org.projectluma.connect"
    compileSdk = 36

    defaultConfig {
        applicationId = "org.projectluma.connect"
        minSdk = 29
        targetSdk = 36
        versionCode = 2
        versionName = "0.2.0"
    }

    signingConfigs {
        releaseSigning?.let { key ->
            create("release") {
                storeFile = file(key.getProperty("storeFile"))
                storePassword = key.getProperty("storePassword")
                keyAlias = key.getProperty("keyAlias")
                keyPassword = key.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            signingConfig = signingConfigs.findByName("release") ?: signingConfigs.getByName("debug")
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures { compose = true }

    // "play" is the Google Play build. "full" adds SMS access (read and send through the
    // phone's SIM for Prairie Messages), which Play allows only under a reviewed exception,
    // and is distributed directly.
    flavorDimensions += "distribution"
    productFlavors {
        create("play") { dimension = "distribution" }
        create("full") {
            dimension = "distribution"
            versionNameSuffix = "-full"
        }
    }

    packaging {
        resources.excludes += setOf("META-INF/versions/9/OSGI-INF/MANIFEST.MF", "META-INF/DEPENDENCIES")
    }

    lint {
        // Luma Connect declares the Android 17 local-network permission ahead of its SDK.
        disable += setOf("UnknownPermission")
    }
}

kotlin {
    compilerOptions { jvmTarget.set(JvmTarget.JVM_17) }
}

dependencies {
    implementation(project(":protocol"))
    implementation(libs.androidx.core)
    implementation(libs.androidx.activity.compose)
    // androidx.biometric pulls Fragment 1.2.x; the Activity Result API needs 1.3.0 or newer.
    implementation(libs.androidx.fragment)
    implementation(libs.androidx.lifecycle.runtime)
    implementation(libs.androidx.lifecycle.compose)
    implementation(platform(libs.compose.bom))
    implementation(libs.compose.ui)
    implementation(libs.compose.foundation)
    implementation(libs.compose.material3)
    implementation(libs.compose.tooling.preview)
    implementation(libs.camera.camera2)
    implementation(libs.camera.lifecycle)
    implementation(libs.camera.view)
    implementation(libs.zxing.core)
    implementation(libs.androidx.biometric)
    testImplementation(libs.junit)
}
