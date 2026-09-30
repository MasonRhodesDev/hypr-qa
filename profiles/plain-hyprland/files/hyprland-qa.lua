
----------------------------------------------------------------------
---- hypr-qa overrides (plain-hyprland profile) ----------------------
-- Everything above is the stock /usr/share/hypr/hyprland.lua of the
-- installed Hyprland (terminal switched to foot). Below: only what QA
-- needs. Later hl.config calls win.
----------------------------------------------------------------------

-- A real XCursor theme (adwaita-cursors, in /usr/share/icons). Without one
-- Hyprland draws its built-in 32x32 fallback pointer. hl.env runs before
-- Hyprland creates its cursor manager, so this is the startup theme, and
-- clients inherit it. No hyprcursor theme is installed: hyprcursor fails
-- ("Hyprcursor failed loading theme") and Hyprland falls back to XCursor.
hl.env("XCURSOR_THEME", "Adwaita")
hl.env("XCURSOR_SIZE", "24")

-- Fixed output: virtio-vga's default mode is 1280x800; pin it anyway.
hl.monitor({
    output   = "Virtual-1",
    mode     = "1280x800@60",
    position = "0x0",
    scale    = 1,
})

hl.config({
    -- No update news / donation nag popups.
    ecosystem = {
        no_update_news  = true,
        no_donation_nag = true,
    },
    -- Plain background: no logo, no splash text, no mascot wallpaper.
    misc = {
        disable_hyprland_logo    = true,
        disable_splash_rendering = true,
        force_default_wallpaper  = 0,
    },
    -- Animations make pixel checks timing-dependent; a scenario can turn
    -- them back on with hl.config({ animations = { enabled = true } }).
    animations = {
        enabled = false,
    },
})
