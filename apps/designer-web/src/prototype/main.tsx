import "../platform";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "../styles.css";
import "./prototype.css";
import { PrototypeApp } from "./PrototypeApp";

createRoot(document.getElementById("root")!).render(
    <StrictMode>
        <PrototypeApp />
    </StrictMode>,
);
