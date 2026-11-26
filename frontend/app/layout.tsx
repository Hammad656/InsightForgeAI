import type { Metadata } from "next";
import BrowserTelemetry from "./browser-telemetry";
import "./globals.css";
import { AppFrame } from "@/components/app-frame";
export const metadata: Metadata = { title: "InsightForge AI", description: "Business intelligence workspace" };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) { return <html lang="en"><body><AppFrame>{children}</AppFrame><BrowserTelemetry /></body></html>; }
