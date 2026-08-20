import { NavLink, Route, Routes } from "react-router-dom";
import {
  ChartLine,
  PlugsConnected,
  PuzzlePiece,
} from "@phosphor-icons/react";
import { LogoMark } from "./components/LogoMark";
import OverviewPage from "./pages/OverviewPage";
import ConnectivityPage from "./pages/ConnectivityPage";
import SkillsPage from "./pages/SkillsPage";

const nav = [
  { to: "/", label: "Overview", icon: ChartLine, end: true },
  { to: "/connectivity", label: "Connectivity", icon: PlugsConnected },
  { to: "/skills", label: "Skills", icon: PuzzlePiece },
];

export default function App() {
  return (
    <div className="min-h-[100dvh] flex flex-col">
      <header className="sticky top-0 z-20 h-16 border-b border-line/80 bg-canvas/90 backdrop-blur-md">
        <div className="mx-auto flex h-full max-w-[1400px] items-center justify-between gap-6 px-4 md:px-6">
          <div className="flex items-center gap-3 min-w-0">
            <LogoMark className="size-8 shrink-0" />
            <div className="min-w-0">
              <div className="text-sm font-semibold tracking-tight truncate">
                Proxy Console
              </div>
              <div className="text-[11px] text-muted truncate">
                运营控制台
              </div>
            </div>
          </div>
          <nav className="flex items-center gap-1">
            {nav.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  [
                    "inline-flex items-center gap-2 rounded-md px-3 py-2 text-sm transition active:scale-[0.98]",
                    isActive
                      ? "bg-panel-2 text-ink border border-line"
                      : "text-muted hover:text-ink hover:bg-panel",
                  ].join(" ")
                }
              >
                <item.icon size={16} weight="bold" />
                <span className="hidden sm:inline">{item.label}</span>
              </NavLink>
            ))}
          </nav>
        </div>
      </header>
      <main className="flex-1 mx-auto w-full max-w-[1400px] px-4 md:px-6 py-6 md:py-8">
        <Routes>
          <Route path="/" element={<OverviewPage />} />
          <Route path="/connectivity" element={<ConnectivityPage />} />
          <Route path="/skills" element={<SkillsPage />} />
        </Routes>
      </main>
    </div>
  );
}
