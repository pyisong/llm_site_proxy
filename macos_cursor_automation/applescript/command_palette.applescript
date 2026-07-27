-- 命令面板：需「辅助功能」授权给执行 osascript 的宿主（Terminal / Python 等）
tell application "Cursor" to activate
delay 0.25
tell application "System Events"
	tell process "Cursor"
		set frontmost to true
	end tell
	keystroke "p" using {command down, shift down}
end tell
