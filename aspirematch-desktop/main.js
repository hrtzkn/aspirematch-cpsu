const { app, BrowserWindow, Menu } = require("electron");
const { autoUpdater } = require("electron-updater");
const path = require("path");

function createWindow() {

  // Splash Screen
  const splash = new BrowserWindow({
    width: 420,
    height: 380,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    resizable: false,
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false
    }
  });

  splash.loadFile("splash.html");

  splash.webContents.on("did-finish-load", () => {
    splash.webContents.send("app_version", app.getVersion());
  });

  // Main Window
  const win = new BrowserWindow({
    width: 1200,
    height: 800,
    show: false,
    icon: path.join(__dirname, "Aspirematch-logo.ico"),
    webPreferences: {
      contextIsolation: true
    }
  });

  // Remove menu bar
  Menu.setApplicationMenu(null);

  // Load your Flask app (Render)
  win.loadURL("https://aspirematch-cpsu.onrender.com/admin");

  // When ready
  win.once("ready-to-show", () => {
    splash.destroy();
    win.show();
  });

  // Auto updater
  autoUpdater.checkForUpdatesAndNotify();
}

app.whenReady().then(createWindow);

// Quit properly on Windows
app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
