import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import UploadCard from "./UploadCard";

const uploadInvoice = vi.fn();
vi.mock("../services/api", async (orig) => {
  const m = await orig<typeof import("../services/api")>();
  return { ...m, api: { ...m.api, uploadInvoice: (f: File) => uploadInvoice(f) } };
});

const ok = { file: "invoices/upload_1_a.pdf", company: "Initech LLC", invoice_number: "INI-1", invoice_date: "2026-09-01",
  due_date: "2026-10-01", amount: "10.00", currency: "USD", warnings: [], suggested_task: "Find the latest invoice from Initech LLC" };

test("uploading a PDF shows the result and can fill the task box", async () => {
  uploadInvoice.mockResolvedValueOnce(ok);
  const onUse = vi.fn(); const onUploaded = vi.fn();
  render(<UploadCard onUploaded={onUploaded} onUse={onUse} disabled={false} />);
  await userEvent.upload(screen.getByLabelText(/choose invoice pdf/i), new File(["%PDF-1.4"], "a.pdf", { type: "application/pdf" }));
  expect(await screen.findByText(/saved as invoices\/upload_1_a.pdf/i)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /use in a task/i }));
  expect(onUse).toHaveBeenCalledWith(ok.suggested_task);
  expect(onUploaded).toHaveBeenCalled();
});

test("an upload error is shown", async () => {
  uploadInvoice.mockRejectedValueOnce(new Error("This file is not a real PDF."));
  render(<UploadCard onUploaded={() => undefined} onUse={() => undefined} disabled={false} />);
  await userEvent.upload(screen.getByLabelText(/choose invoice pdf/i), new File(["x"], "a.pdf", { type: "application/pdf" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("not a real PDF");
});
