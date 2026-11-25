import type { Metadata } from "next";
import BrowserTelemetry from "./browser-telemetry";
import "./globals.css";
export const metadata: Metadata = { title: "InsightForge AI", description: "Business intelligence workspace" };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) { return <html lang="en"><body>{children}<BrowserTelemetry /></body></html>; }
