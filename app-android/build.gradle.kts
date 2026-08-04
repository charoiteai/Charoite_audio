plugins {
    // kotlin.android не объявляем: с AGP 9 поддержка Kotlin встроена в
    // сам плагин приложения, а отдельный плагин падает при применении.
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.kotlin.compose) apply false
}
