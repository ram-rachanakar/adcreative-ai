import { describe, expect, it } from "vitest";
import { colorsValid, parseColors, productEditSchema } from "./validation";

describe("validation", () => {
  it("parses and validates hex colors", () => {
    expect(parseColors("#112233, #AABBCC  #00ff00")).toEqual(["#112233", "#AABBCC", "#00ff00"]);
    expect(colorsValid(["#112233"])).toBe(true);
    expect(colorsValid(["red"])).toBe(false);
    expect(colorsValid(["#12345"])).toBe(false);
  });
  it("requires a product title", () => {
    expect(productEditSchema.safeParse({ title: "" }).success).toBe(false);
    expect(productEditSchema.safeParse({ title: "Bottle" }).success).toBe(true);
  });
});
