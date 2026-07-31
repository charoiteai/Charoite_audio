package ai.charoite.companion

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

/** Те же цвета, что у macOS- и iPhone-приложений: чароит — фиолетовый камень. */
private val Violet = Color(0xFF9B6DFF)
private val VioletDeep = Color(0xFF6C4BD8)
private val Ink = Color(0xFF0E0B14)
private val Paper = Color(0xFFF7F5FB)

private val dark = darkColorScheme(
    primary = Violet,
    onPrimary = Color.White,
    secondary = VioletDeep,
    background = Ink,
    surface = Color(0xFF171125),
    onBackground = Color(0xFFEDE9F6),
    onSurface = Color(0xFFEDE9F6),
)

private val light = lightColorScheme(
    primary = VioletDeep,
    onPrimary = Color.White,
    secondary = Violet,
    background = Paper,
    surface = Color.White,
    onBackground = Color(0xFF1A1524),
    onSurface = Color(0xFF1A1524),
)

@Composable
fun CharoiteTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = if (isSystemInDarkTheme()) dark else light,
        content = content,
    )
}
