import { z } from "zod";
export const hexColor = z.string().regex(/^#[0-9a-fA-F]{6}$/, "Use #RRGGBB");
export const productEditSchema = z.object({
  title: z.string().min(1, "Title is required").max(300),
  description: z.string().max(4000).optional(),
  category: z.string().max(200).optional(),
  brand_name: z.string().max(200).optional(),
  price: z.string().max(50).optional(),
  features: z.string().optional(), // one per line in the form
});
export type ProductEditForm = z.infer<typeof productEditSchema>;
export const parseColors = (s: string) => s.split(/[\s,]+/).filter(Boolean);
export const colorsValid = (cs: string[]) => cs.every(c => hexColor.safeParse(c).success);
