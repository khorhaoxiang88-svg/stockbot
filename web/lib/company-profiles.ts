type CompanyProfile = {
  summary: string;
  source?: string;
};

const PROFILES: Record<string, CompanyProfile> = {
  RCKY: {
    summary: "Designs and sells footwear and apparel, including work, outdoor and western brands.",
    source: "https://investors.rockybrands.com/company-information",
  },
  WLYB: {
    summary: "Publishes research, professional and learning content in print and digital formats.",
    source: "https://www.wiley.com/en-us/about-us/",
  },
  ESCA: {
    summary: "Makes and distributes sporting goods for indoor, outdoor recreation and fitness.",
    source: "https://escaladeinc.com/locations/",
  },
  COKE: {
    summary: "Manufactures, sells and distributes Coca-Cola beverages as the largest U.S. Coca-Cola bottler.",
    source: "https://cocacolabottlingcoconsolidated.gcs-web.com/corporate-profile",
  },
  UTMD: {
    summary: "Develops and manufactures specialty medical devices, especially for women and babies.",
    source: "https://www.utahmed.com/corporate.html",
  },
  PLNT: {
    summary: "Franchises and operates low-cost Planet Fitness gyms.",
    source: "https://investor.planetfitness.com/",
  },
  CBZ: {
    summary: "Provides accounting, tax, advisory, employee benefits, insurance and technology services.",
    source: "https://www.cbiz.com/about",
  },
  MDXG: {
    summary: "Develops placental biologic products for wound care, burns and surgical recovery.",
    source: "https://www.mimedx.com/company-overview/",
  },
};

export function getCompanyProfile(
  symbol: string,
  name: string,
  sicCode: string | null,
): CompanyProfile {
  return PROFILES[symbol] ?? {
    summary: `${name} is a listed company tracked by the bot${sicCode ? ` under SEC industry code ${sicCode}` : ""}.`,
  };
}
