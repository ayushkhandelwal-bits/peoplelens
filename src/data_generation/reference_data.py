"""Reference data for the fictional organisation simulated by PeopleLens.

The company ("Aster Group", entirely fictional) is a diversified consumer
services and technology business operating in seven Indian cities. All amounts
are fictional and expressed in INR. Every multiplier below is a *simulation
assumption* documented in docs/data_dictionary.md, not an empirical estimate.
"""

from __future__ import annotations

COMPANY_NAME = "Aster Group (fictional)"
FOUNDED_YEAR = 2012

LOCATIONS = [
    # id, city, region, office type
    ("LOC01", "Bengaluru", "South", "Headquarters"),
    ("LOC02", "Mumbai", "West", "Regional Office"),
    ("LOC03", "Delhi NCR", "North", "Regional Office"),
    ("LOC04", "Hyderabad", "South", "Technology & Service Hub"),
    ("LOC05", "Pune", "West", "Technology & Service Hub"),
    ("LOC06", "Chennai", "South", "Service Hub"),
    ("LOC07", "Jaipur", "North", "Service Hub"),
]

JOB_LEVELS = ["L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8", "L9"]
LEVEL_TITLES = {
    "L1": "Associate", "L2": "Senior Associate", "L3": "Specialist", "L4": "Team Lead",
    "L5": "Manager", "L6": "Senior Manager", "L7": "Head of Department",
    "L8": "Business Unit Head", "L9": "Chief Executive",
}
MANAGERIAL_LEVELS = {4, 5, 6, 7, 8, 9}

JOB_FAMILIES = [
    "Engineering", "Product & Design", "Data & Analytics", "IT & Security", "Operations",
    "Customer Support", "Sales", "Marketing", "Finance", "Human Resources", "Legal & Compliance",
    "Administration", "Executive",
]

# name, business unit, primary family, location weights {city: w}, size weight,
# attrition factor (relative), planned growth intent, wlb climate
_TECH = {"Bengaluru": 0.5, "Hyderabad": 0.25, "Pune": 0.25}
_CORP = {"Bengaluru": 0.6, "Mumbai": 0.2, "Delhi NCR": 0.2}
_COMM = {"Mumbai": 0.35, "Delhi NCR": 0.3, "Bengaluru": 0.35}
_NATIONAL = {"Bengaluru": 0.3, "Mumbai": 0.15, "Delhi NCR": 0.15, "Hyderabad": 0.15, "Pune": 0.1,
             "Chennai": 0.1, "Jaipur": 0.05}

DEPARTMENTS = [
    # (name, BU, family, locations, size_weight, attrition_factor, growth_intent, wlb)
    ("Platform Engineering", "Technology", "Engineering", _TECH, 1.4, 0.95, 0.10, 0.0),
    ("Mobile Engineering", "Technology", "Engineering", _TECH, 0.9, 1.00, 0.08, 0.0),
    ("Data Engineering", "Technology", "Data & Analytics", _TECH, 0.7, 0.95, 0.12, 0.05),
    ("Data Science & Analytics", "Technology", "Data & Analytics", _TECH, 0.6, 0.90, 0.15, 0.1),
    ("IT Infrastructure", "Technology", "IT & Security", _TECH, 0.6, 0.80, 0.03, 0.1),
    ("Information Security", "Technology", "IT & Security", _TECH, 0.35, 0.80, 0.08, 0.1),
    ("Product Management", "Technology", "Product & Design", _TECH, 0.45, 0.85, 0.07, 0.0),
    ("Design & UX", "Technology", "Product & Design", _TECH, 0.35, 0.90, 0.06, 0.1),
    ("Customer Support", "Operations", "Customer Support",
     {"Jaipur": 0.4, "Hyderabad": 0.3, "Chennai": 0.3}, 3.2, 1.55, 0.05, -0.35),
    ("Service Operations North", "Operations", "Operations",
     {"Delhi NCR": 0.65, "Jaipur": 0.35}, 2.0, 1.25, 0.06, -0.2),
    ("Service Operations South", "Operations", "Operations",
     {"Bengaluru": 0.35, "Chennai": 0.35, "Hyderabad": 0.3}, 2.1, 1.20, 0.07, -0.2),
    ("Service Operations West", "Operations", "Operations", {"Mumbai": 0.55, "Pune": 0.45},
     1.9, 1.20, 0.06, -0.2),
    ("Quality Assurance", "Operations", "Operations", _NATIONAL, 0.7, 0.95, 0.03, 0.0),
    ("Supply & Partner Operations", "Operations", "Operations", _NATIONAL, 1.1, 1.10, 0.05, -0.1),
    ("Procurement", "Operations", "Operations", _CORP, 0.4, 0.80, -0.02, 0.1),
    ("Enterprise Sales", "Commercial", "Sales", _COMM, 0.9, 1.10, 0.09, -0.1),
    ("SMB Sales", "Commercial", "Sales", _NATIONAL, 1.5, 1.40, 0.10, -0.25),
    ("Marketing", "Commercial", "Marketing", _COMM, 0.6, 1.00, 0.05, 0.0),
    ("Growth & Partnerships", "Commercial", "Sales", _COMM, 0.5, 1.05, 0.08, 0.0),
    ("Account Management", "Commercial", "Sales", _COMM, 0.8, 1.00, 0.06, 0.0),
    ("Finance", "Corporate", "Finance", _CORP, 0.6, 0.75, 0.03, 0.1),
    ("Human Resources", "Corporate", "Human Resources", _CORP, 0.6, 0.85, 0.04, 0.1),
    ("Legal & Compliance", "Corporate", "Legal & Compliance", _CORP, 0.25, 0.70, 0.03, 0.15),
    ("Administration & Facilities", "Corporate", "Administration", _NATIONAL, 0.45, 0.90, 0.0, 0.1),
]
EXECUTIVE_OFFICE = ("Executive Office", "Executive", "Executive", {"Bengaluru": 1.0})
BUSINESS_UNITS = ["Technology", "Operations", "Commercial", "Corporate"]

# Secondary job families that appear within departments (support roles).
SECONDARY_FAMILIES = {
    "Technology": ["Engineering", "Data & Analytics", "Product & Design"],
    "Operations": ["Operations", "Customer Support", "Data & Analytics"],
    "Commercial": ["Sales", "Marketing", "Data & Analytics"],
    "Corporate": ["Finance", "Human Resources", "Administration"],
}

# Level mix for non-head roles (default) and department-specific overrides.
LEVEL_MIX_DEFAULT = {1: 0.28, 2: 0.27, 3: 0.20, 4: 0.12, 5: 0.08, 6: 0.05}
LEVEL_MIX_FRONTLINE = {1: 0.46, 2: 0.24, 3: 0.12, 4: 0.10, 5: 0.05, 6: 0.03}
LEVEL_MIX_TECH = {1: 0.18, 2: 0.30, 3: 0.24, 4: 0.14, 5: 0.08, 6: 0.06}
FRONTLINE_DEPTS = {"Customer Support", "Service Operations North", "Service Operations South",
                   "Service Operations West", "SMB Sales", "Supply & Partner Operations"}

# Annual base salary midpoint (INR) by level, before family/location multipliers.
BASE_SALARY_MID = {1: 420_000, 2: 680_000, 3: 1_050_000, 4: 1_650_000, 5: 2_500_000,
                   6: 3_900_000, 7: 6_000_000, 8: 11_000_000, 9: 24_000_000}
FAMILY_PAY_FACTOR = {
    "Engineering": 1.30, "Data & Analytics": 1.25, "Product & Design": 1.22, "IT & Security": 1.10,
    "Operations": 0.85, "Customer Support": 0.72, "Sales": 1.00, "Marketing": 1.00,
    "Finance": 1.00, "Human Resources": 0.95, "Legal & Compliance": 1.05, "Administration": 0.80,
    "Executive": 1.00,
}
LOCATION_PAY_FACTOR = {"Bengaluru": 1.08, "Mumbai": 1.07, "Delhi NCR": 1.04, "Hyderabad": 1.00,
                       "Pune": 0.98, "Chennai": 0.96, "Jaipur": 0.86}
# Annual movement of salary-band midpoints ("market"). Technology talent moves faster.
BAND_INFLATION = {"Engineering": 0.095, "Data & Analytics": 0.095, "Product & Design": 0.08}
BAND_INFLATION_DEFAULT = 0.06
VARIABLE_PAY_PCT = {1: 0.05, 2: 0.06, 3: 0.08, 4: 0.10, 5: 0.12, 6: 0.15, 7: 0.20, 8: 0.25, 9: 0.35}
SALES_VARIABLE_UPLIFT = 0.10
BENEFITS_PCT, BENEFITS_FIXED = 0.07, 36_000
MERIT_INCREASE = {1: 0.0, 2: 0.03, 3: 0.065, 4: 0.095, 5: 0.13}

RECRUITMENT_SOURCES = {
    # weight, cv_shortlist, screen_pass, interview_to_offer, offer_accept, join (no reneging)
    "Employee Referral": (0.14, 0.88, 0.74, 0.52, 0.88, 0.95),
    "Job Portal": (0.30, 0.62, 0.55, 0.38, 0.80, 0.85),
    "Professional Network": (0.22, 0.74, 0.63, 0.44, 0.82, 0.90),
    "Careers Website": (0.16, 0.68, 0.59, 0.42, 0.84, 0.91),
    "Recruitment Agency": (0.12, 0.82, 0.67, 0.48, 0.76, 0.86),
    "Campus": (0.06, 0.72, 0.58, 0.46, 0.90, 0.88),
}
# Early-tenure attrition multiplier by source of hire (first 12 months only).
SOURCE_EARLY_ATTRITION = {"Employee Referral": 0.70, "Job Portal": 1.20, "Professional Network": 0.95,
                          "Careers Website": 1.00, "Recruitment Agency": 1.25, "Campus": 1.05}
SOURCE_FIXED_COST = {"Employee Referral": 40_000, "Job Portal": 15_000, "Professional Network": 22_000,
                     "Careers Website": 8_000, "Recruitment Agency": 0, "Campus": 35_000}
AGENCY_FEE_PCT = 0.0833
RECRUITER_COST_PER_OPEN_DAY = 1_100
ASSESSMENT_COST_PER_INTERVIEW = 1_500

SEASONALITY = {1: 1.05, 2: 0.95, 3: 1.00, 4: 1.25, 5: 1.30, 6: 1.12, 7: 1.00, 8: 0.95,
               9: 0.90, 10: 0.95, 11: 0.85, 12: 0.72}

VOLUNTARY_REASONS = ["Better Opportunity", "Compensation", "Career Growth", "Manager Relationship",
                     "Work-Life Balance", "Relocation", "Higher Education", "Personal Reasons"]
INVOLUNTARY_REASONS = ["Performance", "Misconduct", "Restructuring", "End of Contract"]

TRAINING_PROGRAMS = [
    # name, category, hours, cost, eligibility(level range), business units or None
    ("Code of Conduct & POSH Awareness", "Compliance", 2, 500, (1, 9), None),
    ("Cloud Architecture Fundamentals", "Technical", 24, 28_000, (1, 6), {"Technology"}),
    ("Data Analysis with SQL & Python", "Technical", 20, 18_000, (1, 6), None),
    ("Advanced Excel & Reporting", "Technical", 12, 6_000, (1, 5), {"Operations", "Corporate", "Commercial"}),
    ("Consultative Selling", "Functional", 16, 15_000, (1, 6), {"Commercial"}),
    ("Customer Experience Excellence", "Functional", 10, 6_500, (1, 5), {"Operations"}),
    ("Financial Acumen for Managers", "Functional", 12, 14_000, (4, 7), None),
    ("First-Time Manager Programme", "Leadership", 32, 45_000, (4, 5), None),
    ("Leading Teams Through Change", "Leadership", 20, 38_000, (5, 7), None),
    ("Effective Communication", "Soft Skills", 8, 5_000, (1, 5), None),
    ("Time & Priority Management", "Soft Skills", 6, 4_000, (1, 4), None),
    ("Negotiation Skills", "Soft Skills", 10, 9_000, (2, 7), None),
]

FIRST_NAMES_F = [
    "Aanya", "Aditi", "Ananya", "Anjali", "Bhavna", "Deepika", "Divya", "Esha", "Gauri", "Ishita",
    "Jahnavi", "Kavya", "Keerthi", "Lakshmi", "Meera", "Megha", "Neha", "Nidhi", "Pallavi", "Pooja",
    "Priya", "Radhika", "Riya", "Sakshi", "Sanya", "Shreya", "Sneha", "Swati", "Tanvi", "Trisha",
    "Vaishnavi", "Zoya", "Farah", "Harleen", "Nandini", "Rukmini", "Simran", "Tara", "Uma", "Varsha",
]
FIRST_NAMES_M = [
    "Aarav", "Abhishek", "Aditya", "Akash", "Amit", "Arjun", "Deepak", "Dev", "Gaurav", "Harsh",
    "Ishaan", "Karan", "Kunal", "Manish", "Mohit", "Nikhil", "Pranav", "Rahul", "Rajat", "Rohan",
    "Sahil", "Sandeep", "Siddharth", "Suresh", "Tarun", "Varun", "Vikram", "Vivek", "Yash", "Zaid",
    "Arnav", "Faisal", "Gurpreet", "Joseph", "Karthik", "Naveen", "Pradeep", "Ravi", "Sameer", "Tushar",
]
LAST_NAMES = [
    "Agarwal", "Bhat", "Chatterjee", "Das", "D'Souza", "Fernandes", "Ghosh", "Gupta", "Iyer", "Jain",
    "Joshi", "Kapoor", "Khan", "Kulkarni", "Kumar", "Menon", "Mishra", "Nair", "Patel", "Pillai",
    "Rao", "Reddy", "Saxena", "Sen", "Shah", "Sharma", "Singh", "Srinivasan", "Thomas", "Verma",
    "Bose", "Chopra", "Dutta", "Gill", "Hegde", "Mehta", "Naidu", "Pandey", "Qureshi", "Sethi",
]
