export type AeoProduct = {
  title: string;
  url: string;
  handle: string;
  description: string;
  price: number | null;
  currency: string;
  image: string;
  alt: string;
  seller: string;
  seller_domain: string;
  seller_url: string;
  rating: number | null;
  reviews: number | null;
};

export type AeoRun = { query: string; total: number | null; products: AeoProduct[] };

export type AeoData = {
  brand: string;
  date: string;
  top: number;
  country: string;
  endpoint: string;
  profile: string;
  tgcLogo: string;
  product: { handle: string; title: string; url: string };
  match: { names: string[]; hosts: string[]; myshopify: string };
  runs: AeoRun[];
};
