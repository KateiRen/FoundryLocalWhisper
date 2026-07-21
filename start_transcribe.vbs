' Starts the Foundry Transcribe tray app without showing a console window.
' Double-click this file, or point a shortcut / Startup-folder entry at it.

Set objShell = CreateObject("WScript.Shell")
Set objFSO = CreateObject("Scripting.FileSystemObject")

strScriptDir = objFSO.GetParentFolderName(WScript.ScriptFullName)
objShell.CurrentDirectory = strScriptDir

' 0 = hidden window, False = don't wait for the process to exit
objShell.Run "cmd /c uv run transcribe.py", 0, False
