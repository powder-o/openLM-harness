import { Route, Routes } from "react-router-dom";
import SettingsDialog from "./components/SettingsDialog";
import Home from "./pages/Home";
import NotebookPage from "./pages/NotebookPage";
import ArchivePage from "./pages/ArchivePage";
import { LibraryProvider, useLibrary } from "./library";

function Screens() {
  const library = useLibrary();
  return (
    <>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/notebook/:id" element={<NotebookPage />} />
        <Route path="/archive/:id" element={<ArchivePage />} />
      </Routes>
      {library.settingsOpen && (
        <SettingsDialog
          onClose={() => {
            library.setSettingsOpen(false);
            library.refreshStatus();
          }}
        />
      )}
    </>
  );
}

export default function App() {
  return (
    <LibraryProvider>
      <Screens />
    </LibraryProvider>
  );
}
