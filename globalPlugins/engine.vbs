' Radyocu Audio Engine v1.4
' Watchdog özellikli ses motoru
Option Explicit

Dim WMP, FSO, CmdFile, LastCmd, CurrentCmd, Volume, CmdPath, TimeDiff

' Komut dosyasi yolu Python tarafindan arguman olarak gonderilir
If WScript.Arguments.Count = 0 Then WScript.Quit
CmdPath = WScript.Arguments(0)

On Error Resume Next

Set WMP = CreateObject("WMPlayer.OCX.7")
If Err.Number <> 0 Then WScript.Quit 1

WMP.settings.autoStart = True
WMP.settings.enableErrorDialogs = False
WMP.settings.volume = 50

Set FSO = CreateObject("Scripting.FileSystemObject")
LastCmd = ""

Do
    On Error Resume Next
    
    ' --- WATCHDOG (GÜVENLİK) ---
    ' Eğer komut dosyası 8 saniyedir güncellenmediyse (NVDA çöktüyse) kapan.
    If FSO.FileExists(CmdPath) Then
        Set CmdFile = FSO.GetFile(CmdPath)
        TimeDiff = DateDiff("s", CmdFile.DateLastModified, Now)
        
        If TimeDiff > 8 Then WScript.Quit
        
        ' Komutu Oku
        Set CmdFile = FSO.OpenTextFile(CmdPath, 1)
        If Not CmdFile.AtEndOfStream Then
            CurrentCmd = CmdFile.ReadLine
        End If
        CmdFile.Close
        
        ' Komutu Uygula
        If CurrentCmd <> LastCmd And CurrentCmd <> "" Then
            LastCmd = CurrentCmd
            If Left(CurrentCmd, 5) = "PLAY " Then
                WMP.URL = Mid(CurrentCmd, 6)
                WMP.controls.play
            ElseIf CurrentCmd = "STOP" Then
                WMP.controls.stop
            ElseIf Left(CurrentCmd, 4) = "VOL " Then
                Volume = CInt(Mid(CurrentCmd, 5))
                WMP.settings.volume = Volume
            ElseIf CurrentCmd = "EXIT" Then
                WScript.Quit
            End If
        End If
    Else
        ' Dosya silindiyse kapan
        WScript.Quit
    End If
    
    WScript.Sleep 200
Loop