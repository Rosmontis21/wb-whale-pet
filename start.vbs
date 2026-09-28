' WorkBuddy Whale Pet - silent launcher (no console window)
' Double-click this file to start the pet.
Option Explicit

Dim fso, shell, here, py

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

here = fso.GetParentFolderName(WScript.ScriptFullName)

' 优先用项目内的 .venv，其次用 PATH 里的 pythonw
py = here & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(py) Then
    py = "pythonw.exe"
End If

shell.CurrentDirectory = here
shell.Run """" & py & """ """ & here & "\wb_pet.py""", 0, False
