# coding=utf-8
# Radyocu NVDA Add-on
# Author: Erol Ulker <m.erolulker@gmail.com>
# License: GNU GPL v2

import addonHandler
import globalPluginHandler
import gui
import wx
import os
import sys
import urllib.request
import urllib.parse
import json
import ssl
import threading
import subprocess
import time
import ui
import globalVars
from logHandler import log

# Çeviri sistemini başlat
addonHandler.initTranslation()

# --- AYARLAR ---
EKLENTI_DIR = os.path.dirname(__file__)
VBS_PATH = os.path.join(EKLENTI_DIR, "engine.vbs")
USER_CONFIG_DIR = globalVars.appArgs.configPath
FAVORILER_DOSYASI = os.path.join(USER_CONFIG_DIR, "radyocu_favoriler.json")
TEMP_DIR = os.environ.get('TEMP', os.environ.get('TMP'))
KOMUT_DOSYASI = os.path.join(TEMP_DIR, "radyocu_cmd.txt")

# --- YARDIMCI FONKSİYONLAR ---
def log_yaz(mesaj):
    log.info(f"[Radyocu] {mesaj}")

def siralama_anahtari(radyo_sozlugu):
    try:
        isim = radyo_sozlugu.get("name", "").strip()
        return isim.lower()
    except:
        return ""

def linkten_isim_bul(url):
    try:
        req = urllib.request.Request(url, headers={'Icy-MetaData': '1', 'User-Agent': 'VLC/3.0.0'})
        with urllib.request.urlopen(req, timeout=3) as response:
            headers = response.info()
            radyo_adi = headers.get('icy-name') or headers.get('ice-name') or headers.get('x-audiocast-name')
            if radyo_adi:
                try: return radyo_adi.encode('latin1').decode('utf-8')
                except: return radyo_adi
            else:
                parsed = urllib.parse.urlparse(url)
                return f"Link: {parsed.netloc}"
    except:
        return _("Custom Link")

# --- ARAYÜZ ---
class SeceneklerDialog(wx.Dialog):
    def __init__(self, parent):
        super(SeceneklerDialog, self).__init__(parent, title=_("Options"), size=(450, 500))
        self.parent = parent
        self.CenterOnParent()
        vbox = wx.BoxSizer(wx.VERTICAL)
        self.buttons = {}
        
        btn_data = [
            (_("Delete Selected Radio"), self.on_sil, "btn_del"),
            (_("Clear All Favorites"), self.on_temizle, "btn_clr"),
            (_("Import M3U List"), self.on_ice, "btn_imp"),
            (_("Export M3U List"), self.on_disa, "btn_exp"),
            (_("User Manual"), self.on_kilavuz, "btn_man"),
            (_("About"), self.on_hakkinda, "btn_abt")
        ]

        for label, handler, btn_id in btn_data:
            btn = wx.Button(self, label=label)
            btn.Bind(wx.EVT_BUTTON, handler)
            self.buttons[btn_id] = btn
            vbox.Add(btn, 0, wx.EXPAND | wx.ALL, 5)

        btn_kapat = wx.Button(self, wx.ID_CANCEL, label=_("Close"))
        vbox.Add(btn_kapat, 0, wx.EXPAND | wx.ALL, 5)
        self.SetSizer(vbox)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_tus)

    def on_tus(self, evt):
        if evt.GetKeyCode() == wx.WXK_ESCAPE: self.Close()
        else: evt.Skip()

    def on_sil(self, evt): 
        self.parent.sil_secili()
        self.buttons["btn_del"].SetFocus()

    def on_temizle(self, evt):
        if wx.MessageBox(_("Are you sure you want to delete all favorites?"), _("Confirmation"), wx.YES_NO | wx.ICON_WARNING, self) == wx.YES:
            self.parent.temizle_liste()
        self.buttons["btn_clr"].SetFocus()

    def on_ice(self, evt): 
        self.parent.yukle_liste_dosya(parent_dlg=self)
        self.buttons["btn_imp"].SetFocus()

    def on_disa(self, evt): 
        self.parent.kaydet_liste_dosya(parent_dlg=self)
        self.buttons["btn_exp"].SetFocus()
    
    def on_kilavuz(self, evt):
        # Kılavuz metni PO dosyasından çekilecek (Anahtar: RADYOCU_MANUAL_TEXT)
        ui.browseableMessage(_("RADYOCU_MANUAL_TEXT"), _("User Manual"))
        self.buttons["btn_man"].SetFocus()
        
    def on_hakkinda(self, evt): 
        info = _("Radyocu v1.4.0\nDeveloper: Erol Ulker\nEmail: m.erolulker@gmail.com")
        ui.browseableMessage(info, _("About"))
        self.buttons["btn_abt"].SetFocus()

class RadyocuFrame(wx.Frame):
    def __init__(self):
        style = wx.DEFAULT_FRAME_STYLE | wx.FRAME_FLOAT_ON_PARENT | wx.WANTS_CHARS
        super(RadyocuFrame, self).__init__(gui.mainFrame, title=_("Radyocu"), size=(500, 650), style=style)

        self.panel = wx.Panel(self)
        self.process = None
        self.keep_alive_active = True
        self.current_volume = 50
        self.favoriler = []
        self.arama_sonuclari = []
        self.secili_radyo = {"name":"", "url":""}
        self.aktif_liste_kodu = 2 

        self.arama_zamanlayici = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.on_arama_zamanlayici, self.arama_zamanlayici)

        self.ids = {
            "OYNAT": wx.NewIdRef(), "DURDUR": wx.NewIdRef(),
            "SES_ART": wx.NewIdRef(), "SES_AZALT": wx.NewIdRef(),
            "SONRAKI": wx.NewIdRef(), "ONCEKI": wx.NewIdRef(),
            "GIZLE": wx.NewIdRef()
        }

        self._build_ui()
        self.kisayollar_ata()
        self.Bind(wx.EVT_CHAR_HOOK, self.on_tus_basildi)

        wx.CallLater(100, self.dosyadan_yukle)
        wx.CallLater(500, self.motoru_baslat)
        
        threading.Thread(target=self.keep_alive_loop, daemon=True).start()

        self.CenterOnScreen()
        self.Show()
        self.txt_ara.SetFocus()

    def _build_ui(self):
        vbox = wx.BoxSizer(wx.VERTICAL)
        vbox.Add(wx.StaticText(self.panel, label=_("Search Radio (Name or Link):")), 0, wx.LEFT | wx.TOP, 5)
        hbox = wx.BoxSizer(wx.HORIZONTAL)
        self.txt_ara = wx.TextCtrl(self.panel, style=wx.TE_PROCESS_ENTER)
        btn_bul = wx.Button(self.panel, label=_("Find"))
        hbox.Add(self.txt_ara, 1, wx.EXPAND | wx.RIGHT, 5)
        hbox.Add(btn_bul, 0)
        vbox.Add(hbox, 0, wx.EXPAND | wx.ALL, 5)

        # ETİKETLER DÜZELTİLDİ: Artık PO dosyasından tam karşılıklarını alacaklar
        vbox.Add(wx.StaticText(self.panel, label=_("Search Results:")), 0, wx.LEFT, 5)
        self.liste_arama = wx.ListBox(self.panel)
        vbox.Add(self.liste_arama, 1, wx.EXPAND | wx.ALL, 5)

        vbox.Add(wx.StaticText(self.panel, label=_("My Favorites:")), 0, wx.LEFT, 5)
        self.liste_favori = wx.ListBox(self.panel)
        vbox.Add(self.liste_favori, 1, wx.EXPAND | wx.ALL, 5)

        self.lbl_durum = wx.StaticText(self.panel, label=_("Ready."))
        vbox.Add(self.lbl_durum, 0, wx.ALL, 5)

        hbox_kontrol = wx.BoxSizer(wx.HORIZONTAL)
        btn_oynat = wx.Button(self.panel, label=_("Play (F7)"))
        btn_dur = wx.Button(self.panel, label=_("Stop (F8)"))
        lbl_ses = wx.StaticText(self.panel, label=_("Volume:"))
        self.slider_ses = wx.Slider(self.panel, value=50, minValue=0, maxValue=100)
        
        hbox_kontrol.Add(btn_oynat, 0, wx.RIGHT, 5)
        hbox_kontrol.Add(btn_dur, 0, wx.RIGHT, 10)
        hbox_kontrol.Add(lbl_ses, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        hbox_kontrol.Add(self.slider_ses, 1, wx.EXPAND)
        vbox.Add(hbox_kontrol, 0, wx.EXPAND | wx.ALL, 5)

        hbox_alt = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_sec = wx.Button(self.panel, label=_("Options"))
        btn_gizle = wx.Button(self.panel, label=_("Hide"))
        btn_cikis = wx.Button(self.panel, label=_("Exit"))
        
        hbox_alt.Add(self.btn_sec, 1, wx.RIGHT, 5)
        hbox_alt.Add(btn_gizle, 1, wx.RIGHT, 5)
        hbox_alt.Add(btn_cikis, 1)
        vbox.Add(hbox_alt, 0, wx.EXPAND | wx.ALL, 5)
        self.panel.SetSizer(vbox)

        self.Bind(wx.EVT_TEXT, self.on_yazi_degisti, self.txt_ara)
        self.Bind(wx.EVT_BUTTON, self.on_bul_tikla, btn_bul)
        self.Bind(wx.EVT_TEXT_ENTER, self.on_bul_tikla, self.txt_ara)
        self.Bind(wx.EVT_LISTBOX, self.on_arama_secim, self.liste_arama)
        self.Bind(wx.EVT_LISTBOX, self.on_favori_secim, self.liste_favori)
        self.Bind(wx.EVT_SET_FOCUS, self.on_arama_secim, self.liste_arama)
        self.Bind(wx.EVT_SET_FOCUS, self.on_favori_secim, self.liste_favori)
        self.Bind(wx.EVT_BUTTON, self.oynat, btn_oynat)
        self.Bind(wx.EVT_BUTTON, self.durdur, btn_dur)
        self.Bind(wx.EVT_SLIDER, self.on_slider_degisti, self.slider_ses)
        self.Bind(wx.EVT_BUTTON, self.on_secenekler, self.btn_sec)
        self.Bind(wx.EVT_BUTTON, self.on_gizle, btn_gizle)
        self.Bind(wx.EVT_BUTTON, self.on_cikis, btn_cikis)
        self.Bind(wx.EVT_CLOSE, self.on_cikis)

    def motoru_baslat(self):
        try:
            with open(KOMUT_DOSYASI, "w") as f: f.write("") 
        except: pass
        if os.path.exists(VBS_PATH):
            try: 
                self.process = subprocess.Popen(["wscript", VBS_PATH, KOMUT_DOSYASI], shell=False)
            except: pass

    def komut_gonder(self, cmd):
        try:
            with open(KOMUT_DOSYASI, "w", encoding="utf-8") as f: f.write(cmd)
        except: pass

    def keep_alive_loop(self):
        while self.keep_alive_active:
            try:
                if os.path.exists(KOMUT_DOSYASI):
                    os.utime(KOMUT_DOSYASI, None)
            except: pass
            time.sleep(2)

    def oynat(self, evt=None):
        url = self.secili_radyo.get("url", "")
        if not url:
            ui.message(_("Please select a radio."))
            return
        ad = self.secili_radyo.get("name", _("Unknown"))
        try:
            mevcut_url_ler = [r["url"] for r in self.favoriler]
            if url not in mevcut_url_ler:
                self.favoriler.append(self.secili_radyo.copy())
                self.favoriler.sort(key=siralama_anahtari)
                self.dosyaya_kaydet()
                self.listeyi_guncelle_favori()
        except: pass
        
        durum_metni = f"{_('Playing:')} {ad}"
        self.lbl_durum.SetLabel(durum_metni)
        ui.message(durum_metni)
        self.komut_gonder(f"PLAY {url}")

    def durdur(self, evt=None):
        self.komut_gonder("STOP")
        self.lbl_durum.SetLabel(_("Stopped."))
        ui.message(_("Stopped."))

    def on_slider_degisti(self, evt):
        val = self.slider_ses.GetValue()
        self.current_volume = val
        self.komut_gonder(f"VOL {val}")

    def ses_arttir(self, evt=None):
        self.current_volume = min(100, self.current_volume + 5)
        self.slider_ses.SetValue(self.current_volume)
        self.komut_gonder(f"VOL {self.current_volume}")
        self.lbl_durum.SetLabel(f"{_('Volume:')} %{self.current_volume}")

    def ses_azalt(self, evt=None):
        self.current_volume = max(0, self.current_volume - 5)
        self.slider_ses.SetValue(self.current_volume)
        self.komut_gonder(f"VOL {self.current_volume}")
        self.lbl_durum.SetLabel(f"{_('Volume:')} %{self.current_volume}")

    def on_tus_basildi(self, evt):
        code = evt.GetKeyCode()
        if code == wx.WXK_F5: self.ses_arttir()
        elif code == wx.WXK_F6: self.ses_azalt()
        elif code == wx.WXK_F7: self.oynat()
        elif code == wx.WXK_F8: self.durdur()
        elif code == wx.WXK_F9: self.sonraki()
        elif code == wx.WXK_F10: self.onceki()
        elif code == wx.WXK_ESCAPE: self.on_gizle(None)
        else: evt.Skip()

    def kisayollar_ata(self):
        self.Bind(wx.EVT_MENU, self.ses_arttir, id=self.ids["SES_ART"])
        self.Bind(wx.EVT_MENU, self.ses_azalt, id=self.ids["SES_AZALT"])
        self.Bind(wx.EVT_MENU, self.oynat, id=self.ids["OYNAT"])
        self.Bind(wx.EVT_MENU, self.durdur, id=self.ids["DURDUR"])
        self.Bind(wx.EVT_MENU, self.sonraki, id=self.ids["SONRAKI"])
        self.Bind(wx.EVT_MENU, self.onceki, id=self.ids["ONCEKI"])
        self.Bind(wx.EVT_MENU, self.on_gizle, id=self.ids["GIZLE"])

    def sonraki(self, evt=None): self.gezinti_yap(1)
    def onceki(self, evt=None): self.gezinti_yap(-1)

    def gezinti_yap(self, yon):
        if self.aktif_liste_kodu == 1:
            liste = self.liste_arama; veri = self.arama_sonuclari
        else:
            liste = self.liste_favori; veri = self.favoriler
        if not veri: return
        sel = liste.GetSelection()
        if sel == wx.NOT_FOUND: new_sel = 0
        else: new_sel = (sel + yon) % len(veri)
        liste.SetSelection(new_sel)
        if self.aktif_liste_kodu == 1: self.on_arama_secim(None)
        else: self.on_favori_secim(None)
        ui.message(self.secili_radyo.get("name",""))
        self.oynat()

    def on_yazi_degisti(self, evt):
        if self.arama_zamanlayici.IsRunning(): self.arama_zamanlayici.Stop()
        self.arama_zamanlayici.Start(700, wx.TIMER_ONE_SHOT)
    def on_arama_zamanlayici(self, evt): self.baslat_arama()
    def on_bul_tikla(self, evt): self.baslat_arama()

    def baslat_arama(self):
        kelime = self.txt_ara.GetValue().strip()
        if not kelime: return
        self.lbl_durum.SetLabel(_("Searching..."))
        threading.Thread(target=self.api_ara_thread, args=(kelime,)).start()

    def api_ara_thread(self, kelime):
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            if kelime.startswith("http"):
                bulunan_isim = linkten_isim_bul(kelime)
                wx.CallAfter(self.arama_tamamlandi, [{"name": bulunan_isim, "url_resolved": kelime}])
                return
            url = "http://de1.api.radio-browser.info/json/stations/byname/" + urllib.parse.quote(kelime)
            req = urllib.request.Request(url, headers={"User-Agent":"RadyocuNVDA/1.0"})
            with urllib.request.urlopen(req, timeout=6, context=ctx) as r:
                data = json.load(r)
                wx.CallAfter(self.arama_tamamlandi, data)
        except: wx.CallAfter(self.lbl_durum.SetLabel, _("Error: Connection problem."))

    def arama_tamamlandi(self, data):
        self.liste_arama.Clear()
        self.arama_sonuclari = []
        if not data:
            self.lbl_durum.SetLabel(_("No results."))
            return
        try: data.sort(key=siralama_anahtari)
        except: pass
        for r in data:
            item = {"name": r.get("name",_("Untitled")), "url": r.get("url_resolved","")}
            self.arama_sonuclari.append(item)
            self.liste_arama.Append(item["name"])
        self.lbl_durum.SetLabel(f"{len(data)} {_('found.')}")
        if self.liste_arama.GetCount() > 0:
            self.liste_arama.SetSelection(0)
            self.on_arama_secim(None)

    def sil_secili(self):
        s = self.liste_favori.GetSelection()
        if s != wx.NOT_FOUND:
            del self.favoriler[s]
            self.dosyaya_kaydet()
            self.listeyi_guncelle_favori()
            ui.message(_("Deleted."))

    def temizle_liste(self):
        self.favoriler = []
        self.dosyaya_kaydet()
        self.listeyi_guncelle_favori()
        ui.message(_("Cleared."))

    def listeyi_guncelle_favori(self):
        self.liste_favori.Clear()
        for r in self.favoriler: self.liste_favori.Append(r["name"])

    def dosyadan_yukle(self):
        try:
            if os.path.exists(FAVORILER_DOSYASI):
                with open(FAVORILER_DOSYASI, "r", encoding="utf-8") as f:
                    self.favoriler = json.load(f)
            self.favoriler.sort(key=siralama_anahtari)
        except: self.favoriler = []
        self.listeyi_guncelle_favori()

    def dosyaya_kaydet(self):
        try:
            with open(FAVORILER_DOSYASI, "w", encoding="utf-8") as f:
                json.dump(self.favoriler, f, indent=4, ensure_ascii=False)
        except: pass

    def yukle_liste_dosya(self, parent_dlg=None):
        target = parent_dlg if parent_dlg else self
        with wx.FileDialog(target, _("Import M3U List"), wildcard="M3U (*.m3u)|*.m3u", style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as d:
            if d.ShowModal() == wx.ID_CANCEL: return
            try:
                with open(d.GetPath(), "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
                gecici_isim = _("Unknown")
                for line in lines:
                    line = line.strip()
                    if not line: continue
                    if line.startswith("#EXTINF"):
                        parts = line.split(",", 1)
                        if len(parts) > 1: gecici_isim = parts[1].strip()
                    elif not line.startswith("#"):
                        url = line
                        if not any(r['url'] == url for r in self.favoriler):
                            self.favoriler.append({'name': gecici_isim, 'url': url})
                        gecici_isim = _("Unknown")
                self.favoriler.sort(key=siralama_anahtari)
                self.dosyaya_kaydet()
                self.listeyi_guncelle_favori()
                ui.message(_("Imported."))
            except: ui.message(_("Error occurred."))

    def kaydet_liste_dosya(self, parent_dlg=None):
        target = parent_dlg if parent_dlg else self
        with wx.FileDialog(target, _("Export M3U List"), defaultFile="radyolarim.m3u", wildcard="M3U (*.m3u)|*.m3u", style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT) as d:
            if d.ShowModal() == wx.ID_CANCEL: return
            try:
                with open(d.GetPath(), "w", encoding="utf-8") as f:
                    f.write("#EXTM3U\n")
                    for r in self.favoriler:
                        f.write(f"#EXTINF:-1,{r['name']}\n{r['url']}\n")
                ui.message(_("Saved."))
            except: ui.message(_("Failed."))

    def on_secenekler(self, evt):
        dlg = SeceneklerDialog(self)
        dlg.ShowModal()
        dlg.Destroy()

    def on_gizle(self, evt): self.Hide(); ui.message(_("Hidden."))
    def on_cikis(self, evt): 
        self.keep_alive_active = False
        self.komut_gonder("EXIT")
        self.Destroy()

    def on_arama_secim(self, evt):
        self.aktif_liste_kodu = 1
        if self.liste_arama.GetSelection() != wx.NOT_FOUND:
            self.secili_radyo = self.arama_sonuclari[self.liste_arama.GetSelection()]
        if evt: evt.Skip()

    def on_favori_secim(self, evt):
        self.aktif_liste_kodu = 2
        if self.liste_favori.GetSelection() != wx.NOT_FOUND:
            self.secili_radyo = self.favoriler[self.liste_favori.GetSelection()]
        if evt: evt.Skip()

class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    scriptCategory = "Radyocu"
    def __init__(self):
        super(GlobalPlugin, self).__init__()
        self.wnd = None
        self.menu_item = None
        wx.CallLater(3000, self.menu_ekle)

    def menu_ekle(self):
        try:
            m = gui.mainFrame.sysTrayIcon.toolsMenu
            for i in m.GetMenuItems():
                if i.GetItemLabelText() == "Radyocu": m.DestroyItem(i)
            self.menu_item = m.Append(wx.ID_ANY, "Radyocu")
            gui.mainFrame.sysTrayIcon.Bind(wx.EVT_MENU, lambda e: self.ac(), self.menu_item)
        except: pass

    def ac(self): wx.CallAfter(self._ac_gui)
    def _ac_gui(self):
        if not self.wnd: self.wnd = RadyocuFrame()
        self.wnd.Show()
        self.wnd.Raise()
        try: self.wnd.txt_ara.SetFocus()
        except: pass
    
    def script_ac(self, gesture): self.ac()
    __gestures = {"kb:control+shift+NVDA+z": "ac"}