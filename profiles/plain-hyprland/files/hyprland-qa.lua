hl.env("XCURSOR_THEME", "Adwaita")
hl.env("XCURSOR_SIZE", "24")

hl.monitor({
    output   = "Virtual-1",
    mode     = "1280x800@60",
    position = "0x0",
    scale    = 1,
})

hl.config({
    ecosystem = {
        no_update_news  = true,
        no_donation_nag = true,
    },
    misc = {
        disable_hyprland_logo    = true,
        disable_splash_rendering = true,
        force_default_wallpaper  = 0,
    },
    animations = {
        enabled = false,
    },
})

hl.on("hyprland.start", function()
    hl.exec_cmd("/usr/lib/polkit-gnome/polkit-gnome-authentication-agent-1")
end)
