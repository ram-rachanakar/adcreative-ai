"use client";
import { Modal } from "../controls";
import TemplatesPanel from "./TemplatesPanel";

/** Workflow A: pick a template, choose a format, preview, and create an editable banner for the campaign. */
export default function StartFromTemplateDialog({ campaignId, onClose }: { campaignId: string; onClose: () => void }) {
  return <Modal title="Start a banner from a template" onClose={onClose} wide><TemplatesPanel target={{ kind: "campaign", campaignId }} /></Modal>;
}
