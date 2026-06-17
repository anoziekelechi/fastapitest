'use client';

import { useEffect, useState } from 'react';
import axios from 'axios';

interface HomeData {
  sitename: string;
  intro?: string | null;
  aboutus?: string | null;
  mission?: string | null;
  vision?: string | null;
  logo_url?: string | null;
  image_url?: string | null;
}

export default function Home() {
  const [data, setData] = useState<HomeData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchHome = async () => {
      try {
        const res = await axios.get<HomeData>('http://localhost:8000/home');
        setData(res.data);
      } catch (err) {
        console.error('Failed to load home data');
      } finally {
        setLoading(false);
      }
    };

    fetchHome();
  }, []);

  if (loading) return <div className="text-center py-20">Loading...</div>;
  if (!data) return <div className="text-center py-20">No home data available.</div>;

  return (
    <div className="min-h-screen">
      {/* Hero Banner */}
      {data.image_url && (
        <div className="relative h-[60vh] bg-gray-900">
          <img
            src={data.image_url}
            alt="Hero"
            className="w-full h-full object-cover opacity-80"
          />
          <div className="absolute inset-0 flex items-center justify-center text-center text-white">
            <div>
              <h1 className="text-5xl md:text-6xl font-bold mb-4">{data.sitename}</h1>
              {data.intro && <p className="text-xl md:text-2xl max-w-2xl mx-auto">{data.intro}</p>}
            </div>
          </div>
        </div>
      )}

      {/* About Us */}
      {data.aboutus && (
        <div className="py-16 px-6 max-w-4xl mx-auto">
          <h2 className="text-3xl font-bold mb-6">About Us</h2>
          <p className="text-lg leading-relaxed text-gray-700">{data.aboutus}</p>
        </div>
      )}

      {/* Mission & Vision */}
      <div className="bg-gray-50 py-16">
        <div className="max-w-5xl mx-auto grid md:grid-cols-2 gap-10 px-6">
          {data.mission && (
            <div>
              <h3 className="text-2xl font-semibold mb-4">Our Mission</h3>
              <p className="text-gray-600 leading-relaxed">{data.mission}</p>
            </div>
          )}
          {data.vision && (
            <div>
              <h3 className="text-2xl font-semibold mb-4">Our Vision</h3>
              <p className="text-gray-600 leading-relaxed">{data.vision}</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
